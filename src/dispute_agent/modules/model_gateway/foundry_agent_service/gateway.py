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
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import AgentDrift, GuardrailBlocked, TransientPortError
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import definition_hash
from dispute_agent.modules.model_gateway.output import RawReply, complete_with_one_retry

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
        return complete_with_one_retry(
            req, lambda r: self._call(agent, user, r.max_output_tokens), route=ROUTE
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

    def _call(self, agent: AgentLockEntry, user: str, max_output_tokens: int) -> RawReply:
        started = self.monotonic()
        reply = self._with_retries(agent, user, max_output_tokens)
        self.sent_log.append(reply.sent)
        latency_ms = int((self.monotonic() - started) * 1000)

        if reply.status == "incomplete" and "content_filter" in (reply.incomplete_reason or ""):
            raise GuardrailBlocked("the deployment's content filter stopped the answer")
        # A response cut short (max_output_tokens) is not an answer (foundry.md section 6):
        # an empty text makes it invalid, and it gets the one retry like any other.
        text = reply.text if reply.status == "completed" else ""

        tokens_in = reply.input_tokens
        tokens_out = reply.output_tokens
        if tokens_in is None or tokens_out is None:
            tokens_in = len(user) // CHARS_PER_TOKEN_WORST + 1
            tokens_out = max_output_tokens
        return RawReply(
            text=text,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=latency_ms,
            cost_usd=self.price.cost(tokens_in, tokens_out),
            model_id=reply.model or agent.deployment,
        )

    def _with_retries(
        self, agent: AgentLockEntry, user: str, max_output_tokens: int
    ) -> ProviderReply:
        for attempt in range(IN_ADAPTER_RETRIES + 1):
            try:
                return self.client.respond(agent, user, max_output_tokens)
            except TransientPortError as transient:
                if attempt == IN_ADAPTER_RETRIES:
                    raise
                wait = transient.retry_after_s or BACKOFF_S[attempt]
                self.sleep(min(wait, MAX_WAIT_S))
        raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["ROUTE", "FoundryAgentServiceGateway"]
