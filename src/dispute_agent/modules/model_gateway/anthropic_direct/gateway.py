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
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    ConfigError,
    KernelError,
    PermanentPortError,
    TransientPortError,
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

    def complete(self, req: ModelRequest) -> ModelResponse:
        if req.image is not None:
            # ADR 0049: only the direct deployment route reads photos. Answering without the
            # photo would be a verdict about nothing, so refuse.
            raise ConfigError(
                "this model route cannot read images; the image check uses foundry_openai"
            )
        system = "\n\n".join(m.content for m in req.prompt.messages if m.role == "system")
        user = req.prompt.messages[-1].content
        prompt_chars = sum(len(m.content) for m in req.prompt.messages)
        return complete_with_one_retry(
            req, lambda r: self._call(system, user, r.max_output_tokens, prompt_chars), route=ROUTE
        )

    def _call(self, system: str, user: str, max_tokens: int, prompt_chars: int) -> RawReply:
        sent: dict[str, object] = {"model": self.model, "max_tokens": max_tokens}
        self.sent_log.append(sent)
        started = self.monotonic()
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            mapped = map_error(exc)
            if mapped is None:
                raise
            if mapped.maybe_billed:
                worst_in = prompt_chars // CHARS_PER_TOKEN_WORST + 1
                mapped.usage = _usage(worst_in, max_tokens, 0, self.price)
            raise mapped from None
        latency_ms = int((self.monotonic() - started) * 1000)
        stop = str(getattr(response, "stop_reason", "") or "")
        text = "".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", "") == "text"
        )
        usage = getattr(response, "usage", None)
        reported_in: Any = getattr(usage, "input_tokens", None)
        reported_out: Any = getattr(usage, "output_tokens", None)
        if isinstance(reported_in, int) and isinstance(reported_out, int):
            tokens_in, tokens_out = reported_in, reported_out
        else:
            tokens_in, tokens_out = prompt_chars // CHARS_PER_TOKEN_WORST + 1, max_tokens
        unusable = None
        if stop in ("refusal", "max_tokens"):
            # Anthropic structured-outputs guidance: both mean the answer is not usable.
            unusable = f"cut short ({stop})"
        return RawReply(
            text=text,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=latency_ms,
            cost_usd=self.price.cost(tokens_in, tokens_out),
            model_id=str(getattr(response, "model", "") or self.model),
            unusable=unusable,
        )


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
