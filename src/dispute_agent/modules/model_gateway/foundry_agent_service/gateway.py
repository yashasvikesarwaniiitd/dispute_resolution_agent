"""The Foundry Agent Service route (P4b-03, foundry.md sections 3, 4, 6, 7, 10).

One call, in order:

1. **Drift check.** The pinned agent version is read back from Foundry and fingerprinted;
   it must match the lock file, and the lock must match the prompt now in `prompts/`.
   Anything else is `AgentDrift`: the case goes to a person, and the call is not made. The
   result is cached for `drift_cache_s` (foundry.md section 4), so a burst of cases costs
   one read, not one per case.
2. **The call**, with the exact version pinned. Rate limits and server errors are retried
   twice in here, honouring the server's `retry-after`, because a model call has no side
   effects - retrying it only costs money, and every attempt is costed.
3. **The answer** goes through the same checker as every route (`output.py`): JSON, the
   prompt's schema, one retry.
4. **The cost** is computed from the reported usage and the owner's price row. If usage is
   missing, the call is charged at its worst case (ADR 0029), so missing data can only make
   the spending cap stricter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    GuardrailBlocked,
    KernelError,
    TransientPortError,
)
from dispute_agent.kernel.domain.reasoning import ModelUsage
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import definition_hash
from dispute_agent.modules.model_gateway.output import (
    RawReply,
    add_usage,
    complete_with_one_retry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
        AgentServiceClient,
        ProviderReply,
    )
    from dispute_agent.modules.model_gateway.foundry_agent_service.lock import (
        AgentLock,
        AgentLockEntry,
    )
    from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

ROUTE = "foundry_agent_service"
#: foundry.md section 7: two in-adapter retries for 429 and 5xx, then TransientPortError.
IN_ADAPTER_RETRIES = 2
BACKOFF_S = (2.0, 8.0)
MAX_WAIT_S = 30.0
#: Worst-case token estimate when usage is missing: a token is rarely under 3 characters.
CHARS_PER_TOKEN_WORST = 3


@dataclass
class FoundryAgentServiceGateway:
    """Implements ModelGateway."""

    client: AgentServiceClient
    lock: AgentLock
    price: Price
    drift_cache_s: float = 60.0
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic
    sent_log: list[dict[str, object]] = field(default_factory=list)
    _verified: dict[str, float] = field(default_factory=dict)

    def complete(self, req: ModelRequest) -> ModelResponse:
        agent = self._checked_agent(req)
        user = req.prompt.messages[-1].content
        # The agent's instructions are billed as input too, though we do not send them;
        # the worst-case estimate counts every message (review, LOW).
        prompt_chars = sum(len(m.content) for m in req.prompt.messages)
        return complete_with_one_retry(
            req,
            lambda r: self._call(agent, user, r.max_output_tokens, prompt_chars),
            route=ROUTE,
        )

    # -- drift ---------------------------------------------------------------------------------

    def _checked_agent(self, req: ModelRequest) -> AgentLockEntry:
        try:
            agent = self.lock.entry(req.prompt.name)
        except Exception as missing:  # ConfigError: nothing synced for this prompt
            raise AgentDrift(str(missing)) from None
        if (agent.prompt_version, agent.prompt_hash) != (req.prompt.version, str(req.prompt.hash)):
            # The prompt in the repo changed since the agent was synced. The agent would be
            # answering with instructions nobody has reviewed against this code.
            raise AgentDrift(
                f"{req.prompt.name}/{req.prompt.version} in prompts/ is not what "
                f"{agent.agent_name} v{agent.version} was synced from; run `make agents-sync`"
            )
        key = f"{agent.agent_name}@{agent.version}"
        checked_at = self._verified.get(key)
        if checked_at is not None and self.monotonic() - checked_at < self.drift_cache_s:
            return agent
        deployed = self.client.get_version(agent.agent_name, agent.version)
        if definition_hash(model=deployed.model, instructions=deployed.instructions) != (
            agent.definition_hash
        ):
            raise AgentDrift(
                f"{agent.agent_name} v{agent.version} in Foundry no longer matches the lock "
                "file (edited in the portal?)"
            )
        self._verified[key] = self.monotonic()
        return agent

    # -- one provider call -----------------------------------------------------------------------

    def _worst(self, prompt_chars: int, max_output_tokens: int) -> ModelUsage:
        """What one call costs at most: every prompt token, and every output token allowed."""
        tokens_in = prompt_chars // CHARS_PER_TOKEN_WORST + 1
        return ModelUsage(
            input_tokens=tokens_in,
            output_tokens=max_output_tokens,
            latency_ms=0,
            cost_usd=self.price.cost(tokens_in, max_output_tokens),
        )

    def _call(
        self, agent: AgentLockEntry, user: str, max_output_tokens: int, prompt_chars: int
    ) -> RawReply:
        started = self.monotonic()
        reply, failed_cost = self._with_retries(agent, user, max_output_tokens, prompt_chars)
        self.sent_log.append(reply.sent)
        latency_ms = int((self.monotonic() - started) * 1000)

        if reply.status == "incomplete" and "content_filter" in (reply.incomplete_reason or ""):
            # Stopped mid-answer: the generation up to the stop was billed (review, M1).
            blocked = GuardrailBlocked("the deployment's content filter stopped the answer")
            blocked.usage = add_usage(failed_cost, self._worst(prompt_chars, max_output_tokens))
            raise blocked
        # A response cut short (max_output_tokens) is not an answer (foundry.md section 6):
        # an empty text makes it invalid, and it gets the one retry like any other.
        text = reply.text if reply.status == "completed" else ""

        tokens_in = reply.input_tokens
        tokens_out = reply.output_tokens
        if tokens_in is None or tokens_out is None:
            worst = self._worst(prompt_chars, max_output_tokens)
            tokens_in, tokens_out = worst.input_tokens, worst.output_tokens
        # Earlier attempts in this call that may have been billed are carried on this reply,
        # so a success after a timeout still records the timeout's cost.
        return RawReply(
            text=text,
            input_tokens=tokens_in + failed_cost.input_tokens,
            output_tokens=tokens_out + failed_cost.output_tokens,
            latency_ms=latency_ms,
            cost_usd=self.price.cost(tokens_in, tokens_out) + failed_cost.cost_usd,
            model_id=reply.model or agent.deployment,
        )

    def _with_retries(
        self, agent: AgentLockEntry, user: str, max_output_tokens: int, prompt_chars: int
    ) -> tuple[ProviderReply, ModelUsage]:
        """The reply, and the worst-case cost of earlier attempts that may have been billed."""
        failed_cost = ModelUsage(input_tokens=0, output_tokens=0, latency_ms=0, cost_usd=Decimal(0))
        for attempt in range(IN_ADAPTER_RETRIES + 1):
            try:
                return self.client.respond(agent, user, max_output_tokens), failed_cost
            except KernelError as failed:
                if failed.maybe_billed:
                    failed_cost = add_usage(
                        failed_cost, self._worst(prompt_chars, max_output_tokens)
                    )
                if not isinstance(failed, TransientPortError) or attempt == IN_ADAPTER_RETRIES:
                    failed.usage = add_usage(failed_cost, failed.usage)
                    raise
                wait = failed.retry_after_s or BACKOFF_S[attempt]
                self.sleep(min(wait, MAX_WAIT_S))
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ROUTE", "FoundryAgentServiceGateway"]
