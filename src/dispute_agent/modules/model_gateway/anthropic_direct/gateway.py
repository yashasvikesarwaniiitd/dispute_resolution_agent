"""The secondary route: Anthropic API, Haiku only (P4-10, D17, I14).

Used only when the Foundry primary is *unavailable* (failover rule F01, applied by the
kernel's guarded gateway). It holds the project's only provider API key, so three rules are
in code, not configuration:

- **Haiku only.** The model id must match `claude-haiku-*`; anything else is refused when the
  route is built, naming the setting, so a typo in `.env` cannot quietly route cases to an
  expensive model.
- **The key is read once**, by the settings loader, and handed in as a `SecretStr`. It is
  unwrapped only to construct the SDK client, and never formatted into a log, an error or
  an exception message.
- **No content leaves in logs.** Errors name the kind of failure and the status code only.

No sampling parameters are sent (the current API rejects them); retries on 429 and 5xx are
the SDK's own (two, with backoff), and a timeout counts as possibly billed.
"""

from __future__ import annotations

import fnmatch
import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    ConfigError,
    KernelError,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.reasoning import PROMPT_CACHE_BREAK
from dispute_agent.modules.model_gateway.anthropic_direct.structured import (
    provider_schema,
    restore,
)
from dispute_agent.modules.model_gateway.output import RawReply, complete_with_one_retry

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import SecretStr

    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

ROUTE = "anthropic_direct"
ALLOWED_MODELS = "claude-haiku-*"
CHARS_PER_TOKEN_WORST = 3


def check_model(model: str | None) -> str:
    if not model or not fnmatch.fnmatch(model, ALLOWED_MODELS):
        raise ConfigError(
            f"ANTHROPIC_MODEL_HAIKU must be a Haiku model ({ALLOWED_MODELS}); "
            f"{model!r} is refused (I14: the secondary route is Haiku only)"
        )
    return model


@dataclass
class AnthropicDirectGateway:
    """Implements ModelGateway."""

    model: str
    price: Price
    client: Any
    monotonic: Callable[[], float] = time.monotonic
    sent_log: list[dict[str, object]] = field(default_factory=list)
    #: Schemas the API refused once: sent without a format from then on (process memory).
    unsupported: set[str] = field(default_factory=set)
    structured_outputs: bool = True

    def __post_init__(self) -> None:
        check_model(self.model)

    @classmethod
    def build(
        cls, *, model: str | None, api_key: SecretStr | None, price: Price, timeout_s: float
    ) -> AnthropicDirectGateway:
        import anthropic

        checked = check_model(model)
        if api_key is None:
            raise ConfigError("ANTHROPIC_API_KEY is not set; the Haiku route cannot start")
        client = anthropic.Anthropic(
            api_key=api_key.get_secret_value(), timeout=timeout_s, max_retries=2
        )
        return cls(model=checked, price=price, client=client)

    def _native(self, schema: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[Any]]:
        """The grammar for this schema (structured.py), or (None, []) to send it as before."""
        if not self.structured_outputs or not schema or _key(schema) in self.unsupported:
            return None, []
        return provider_schema(schema)

    def complete(self, req: ModelRequest) -> ModelResponse:
        if req.image is not None:
            # ADR 0049: only the direct deployment route reads photos. Answering without the
            # photo would be a verdict about nothing, so refuse.
            raise ConfigError(
                "this model route cannot read images; the image check uses foundry_openai"
            )
        prompt_chars = sum(len(m.content) for m in req.prompt.messages)
        schema = dict(req.prompt.output_schema)
        # Read from the request each attempt: a retry carries what was wrong (output.py).
        return complete_with_one_retry(
            req,
            lambda r: self._call(
                "\n\n".join(m.content for m in r.prompt.messages if m.role == "system"),
                r.prompt.messages[-1].content,
                r.max_output_tokens,
                prompt_chars,
                schema,
            ),
            route=ROUTE,
        )

    def _call(
        self,
        system: str,
        user: str,
        max_tokens: int,
        prompt_chars: int,
        schema: dict[str, Any] | None = None,
    ) -> RawReply:
        native, pairs = self._native(schema)
        # Which way the answer's format was enforced, for the run record (ADR 0073).
        if native is not None:
            mode = "native"
        elif not schema or not self.structured_outputs:
            mode = "plain"
        elif _key(schema) in self.unsupported:
            mode = "fallback_remembered"
        else:
            mode = "fallback_limits"
        sent: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "structured": native is not None,
            "mode": mode,
            "schema": str((schema or {}).get("title", "")),
        }
        self.sent_log.append(sent)
        started = self.monotonic()
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": _system(system),
            "messages": [{"role": "user", "content": _user(user)}],
        }
        try:
            try:
                if native is None:
                    response = self.client.messages.create(**request)
                else:
                    response = self.client.messages.create(
                        **request,
                        output_config={"format": {"type": "json_schema", "schema": native}},
                    )
            except Exception as refused:
                if native is None or not _schema_refused(refused):
                    raise
                # The API refused this schema (400, nothing billed): remember it and answer
                # without the format, as before; parse() still checks every answer.
                self.unsupported.add(_key(schema))
                sent["structured"] = False
                sent["mode"] = "fallback_refused"
                native, pairs = None, []
                response = self.client.messages.create(**request)
        except Exception as exc:
            mapped = map_error(exc)
            sent["error"] = type(exc).__name__
            if mapped is None:
                raise
            if mapped.maybe_billed:
                worst_in = prompt_chars // CHARS_PER_TOKEN_WORST + 1
                mapped.usage = _usage(worst_in, max_tokens, 0, self.price)
            raise mapped from None
        latency_ms = int((self.monotonic() - started) * 1000)
        stop = str(getattr(response, "stop_reason", "") or "")
        sent["latency_ms"], sent["stop"] = latency_ms, stop
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        reported_in: Any = getattr(usage, "input_tokens", None)
        reported_out: Any = getattr(usage, "output_tokens", None)
        written = _count(getattr(usage, "cache_creation_input_tokens", None))
        read = _count(getattr(usage, "cache_read_input_tokens", None))
        if isinstance(reported_in, int) and isinstance(reported_out, int):
            tokens_in, tokens_out = reported_in, reported_out
        else:
            tokens_in, tokens_out = prompt_chars // CHARS_PER_TOKEN_WORST + 1, max_tokens
        text = restore(text, pairs)
        unusable = None
        if stop in ("refusal", "max_tokens"):
            # Anthropic structured-outputs guidance: both mean the answer is not usable.
            unusable = f"cut short ({stop})"
        return RawReply(
            text=text,
            # Every prompt token the call consumed: uncached, written and read (ADR 0072).
            input_tokens=tokens_in + written + read,
            output_tokens=tokens_out,
            latency_ms=latency_ms,
            cost_usd=cached_cost(self.price, tokens_in, written, read, tokens_out),
            cache_write_tokens=written,
            cache_read_tokens=read,
            model_id=str(getattr(response, "model", "") or self.model),
            unusable=unusable,
        )


#: A system prompt at least this long is marked for the prompt cache. The model's minimum
#: cacheable prefix is 512 tokens; shorter ones silently do not cache, and marking them costs
#: nothing, but there is no point (ADR 0072).
CACHE_MIN_CHARS = 2400
#: The provider's cache prices, as multiples of the input price (5-minute cache).
CACHE_WRITE_FACTOR = Decimal("1.25")
CACHE_READ_FACTOR = Decimal("0.1")


def _system(system: str) -> str | list[dict[str, object]]:
    """The system prompt, marked for the prompt cache when it is long enough to be cached. It
    is the stable part of every call (the case data is in the user message), so repeat calls
    within five minutes - an investigation's turns, or the next case - read it from cache."""
    if len(system) < CACHE_MIN_CHARS:
        return system
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _user(user: str) -> str | list[dict[str, object]]:
    """The user message, its case briefing marked for the prompt cache when the prompt marks
    where the briefing ends and the briefing is long enough to cache (audit AUD-16). An
    investigation's turns then read the briefing from cache instead of paying for it again."""
    head, marker, tail = user.partition(PROMPT_CACHE_BREAK)
    if not marker or len(head) < CACHE_MIN_CHARS:
        return user
    return [
        {"type": "text", "text": head + marker, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": tail},
    ]


def _key(schema: dict[str, Any] | None) -> str:
    return json.dumps(schema, sort_keys=True, default=str)


def _schema_refused(exc: BaseException) -> bool:
    """A 400 about the output format: the schema, not the request, is the problem."""
    import anthropic

    if not isinstance(exc, anthropic.BadRequestError):
        return False
    said = str(exc).lower()
    return any(w in said for w in ("output_config", "schema", "format", "grammar"))


def _count(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def cached_cost(price: Price, uncached: int, written: int, read: int, output: int) -> Decimal:
    """What the call costs at the provider's cache rates, so the spending cap stays true."""
    return (
        Decimal(uncached) * price.input_per_mtok
        + Decimal(written) * price.input_per_mtok * CACHE_WRITE_FACTOR
        + Decimal(read) * price.input_per_mtok * CACHE_READ_FACTOR
        + Decimal(output) * price.output_per_mtok
    ) / Decimal(1_000_000)


def _usage(tokens_in: int, tokens_out: int, latency_ms: int, price: Price) -> object:
    from dispute_agent.kernel.domain.reasoning import ModelUsage

    return ModelUsage(
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        latency_ms=latency_ms,
        cost_usd=price.cost(tokens_in, tokens_out),
    )


def map_error(exc: BaseException) -> KernelError | None:
    """Anthropic's typed errors onto the kernel's. Never quotes the request or the key."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return AuthPortError("the Anthropic API refused the key", status=exc.status_code)
    if isinstance(exc, anthropic.RateLimitError):
        retry = exc.response.headers.get("retry-after") if exc.response is not None else None
        try:
            retry_s = float(retry) if retry is not None else None
        except ValueError:
            retry_s = None
        return TransientPortError("rate limited", retry_after_s=retry_s)
    if isinstance(exc, anthropic.APITimeoutError):
        timed_out = TransientPortError("the Anthropic API did not answer in time")
        timed_out.maybe_billed = True
        return timed_out
    if isinstance(exc, anthropic.APIConnectionError):
        return TransientPortError("could not reach the Anthropic API")
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code >= 500:
            failed = TransientPortError(f"the Anthropic API returned {exc.status_code}")
            failed.maybe_billed = True
            return failed
        return PermanentPortError(
            f"the Anthropic API refused the request ({exc.status_code})", status=exc.status_code
        )
    if isinstance(exc, anthropic.AnthropicError):
        unknown = PermanentPortError(f"unexpected Anthropic error ({type(exc).__name__})")
        unknown.maybe_billed = True
        return unknown
    return None


__all__ = ["ALLOWED_MODELS", "ROUTE", "AnthropicDirectGateway", "check_model", "map_error"]
