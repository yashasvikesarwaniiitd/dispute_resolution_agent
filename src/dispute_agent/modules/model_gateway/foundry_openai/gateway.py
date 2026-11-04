"""The same Foundry deployment, called directly, without an agent (P4b-05, foundry.md section 5).

An alternative primary, selected in `config/modules.yaml` - not an automatic failover target
(F01's only failover is to Haiku). It exists for two reasons: an A/B comparison against the
agent route in the live evals, and as the documented fallback design if agent answers prove
unreliable, because structured outputs are documented for gpt-5-mini on this path.

The system prompt is sent as `instructions` on every call (there is no agent holding it), so
there is no drift check: the instructions are, by construction, the ones in `prompts/`.
Everything else - Entra ID, `store=False`, no sampling parameters, output checked in code,
worst-case costing when usage is missing - is the agent route's, and so is the error table.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import ConfigError, GuardrailBlocked
from dispute_agent.kernel.domain.reasoning import ModelUsage
from dispute_agent.modules.model_gateway.foundry_agent_service.client import map_error
from dispute_agent.modules.model_gateway.output import RawReply, complete_with_one_retry

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

ROUTE = "foundry_openai"
CHARS_PER_TOKEN_WORST = 3


@dataclass
class FoundryOpenAiGateway:
    """Implements ModelGateway."""

    deployment: str
    price: Price
    client: Any
    monotonic: Callable[[], float] = time.monotonic
    sent_log: list[dict[str, object]] = field(default_factory=list)

    @classmethod
    def build(
        cls, *, endpoint: str, deployment: str, credential: Any, price: Price, timeout_s: float
    ) -> FoundryOpenAiGateway:
        from azure.ai.projects import AIProjectClient

        if "/api/projects/" not in endpoint:
            raise ConfigError("FOUNDRY_PROJECT_ENDPOINT must be the project endpoint")
        project = AIProjectClient(endpoint=endpoint, credential=credential)
        client = project.get_openai_client(timeout=timeout_s, max_retries=2)
        return cls(deployment=deployment, price=price, client=client)

    def complete(self, req: ModelRequest) -> ModelResponse:
        system = "\n\n".join(m.content for m in req.prompt.messages if m.role == "system")
        user = req.prompt.messages[-1].content
        prompt_chars = sum(len(m.content) for m in req.prompt.messages)
        return complete_with_one_retry(
            req,
            lambda r: self._call(system, user, r.max_output_tokens, prompt_chars),
            route=ROUTE,
        )

    def _worst(self, prompt_chars: int, max_output_tokens: int) -> ModelUsage:
        """What one call costs at most: every prompt token, and every output token allowed."""
        tokens_in = prompt_chars // CHARS_PER_TOKEN_WORST + 1
        return ModelUsage(
            input_tokens=tokens_in,
            output_tokens=max_output_tokens,
            latency_ms=0,
            cost_usd=self.price.cost(tokens_in, max_output_tokens),
        )

    def _call(self, system: str, user: str, max_tokens: int, prompt_chars: int) -> RawReply:
        sent: dict[str, object] = {
            "model": self.deployment,
            "max_output_tokens": max_tokens,
            "store": False,
        }
        self.sent_log.append(sent)
        started = self.monotonic()
        try:
            response = self.client.responses.create(instructions=system, input=user, **sent)
        except Exception as exc:
            mapped = map_error(exc)
            if mapped is None:
                raise
            if mapped.maybe_billed and mapped.usage is None:
                # Azure may still charge for a generation it never returned. Charge the worst
                # case, so a run of timeouts cannot spend past the cap unseen (review, 4).
                mapped.usage = self._worst(prompt_chars, max_tokens)
            raise mapped from None
        latency_ms = int((self.monotonic() - started) * 1000)
        status = str(getattr(response, "status", "") or "")
        details = getattr(response, "incomplete_details", None)
        reason = str(getattr(details, "reason", "") or "")
        usage = getattr(response, "usage", None)
        reported_in: Any = getattr(usage, "input_tokens", None)
        reported_out: Any = getattr(usage, "output_tokens", None)
        if isinstance(reported_in, int) and isinstance(reported_out, int):
            tokens_in, tokens_out = reported_in, reported_out
        else:
            tokens_in, tokens_out = prompt_chars // CHARS_PER_TOKEN_WORST + 1, max_tokens
        if status == "incomplete" and "content_filter" in reason:
            # A filtered answer is still a billed answer: carry its cost (review, 4).
            blocked = GuardrailBlocked("the deployment's content filter stopped the answer")
            blocked.usage = ModelUsage(
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                latency_ms=int((self.monotonic() - started) * 1000),
                cost_usd=self.price.cost(tokens_in, tokens_out),
            )
            raise blocked
        return RawReply(
            text=str(getattr(response, "output_text", "") or "") if status == "completed" else "",
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=latency_ms,
            cost_usd=self.price.cost(tokens_in, tokens_out),
            model_id=str(getattr(response, "model", "") or self.deployment),
            unusable=None if status == "completed" else f"cut short ({reason or status})",
        )


__all__ = ["ROUTE", "FoundryOpenAiGateway"]
