"""A stand-in for Azure behind `AgentServiceClient`, for the Foundry route's offline tests.

It plays the service, not the gateway: it stores agent versions, answers `respond` from a
script, and raises the *kernel's* typed errors - which is what `AzureAgentServiceClient`
turns the SDK's exceptions into. So everything above the client (drift check, retries,
answer checking, costing) runs for real against it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    AuthPortError,
    GuardrailBlocked,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
    DeployedVersion,
    ProviderReply,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.gateway import (
    FoundryAgentServiceGateway,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import (
    AgentLock,
    AgentLockEntry,
    definition_hash,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import RenderedPrompt

DEPLOYMENT = "gpt-5-mini"
FAULTS = ("guardrail", "rate_limit", "auth", "unavailable")
PRICE = Price(input_per_mtok=Decimal("0.25"), output_per_mtok=Decimal("2.00"), source="test")


@dataclass
class StubAgentService:
    """Implements AgentServiceClient."""

    versions: dict[tuple[str, str], DeployedVersion] = field(default_factory=dict)
    script: list[Any] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    reads: int = 0
    report_usage: bool = True
    last: str = "unavailable"

    def get_version(self, agent_name: str, version: str) -> DeployedVersion:
        self.reads += 1
        found = self.versions.get((agent_name, version))
        if found is None:
            raise AgentDrift("the pinned agent version does not exist in Foundry")
        return found

    def create_version(self, agent_name: str, model: str, instructions: str) -> str:
        version = str(1 + sum(1 for name, _ in self.versions if name == agent_name))
        self.versions[(agent_name, version)] = DeployedVersion(model, instructions)
        return version

    def respond(
        self, agent: AgentLockEntry, user_input: str, max_output_tokens: int
    ) -> ProviderReply:
        sent = {
            "max_output_tokens": max_output_tokens,
            "store": False,
            "extra_body": {"agent_reference": {"name": agent.agent_name, "version": agent.version}},
        }
        self.calls.append(sent)
        # A service that is failing keeps failing: once the script runs out, the last fault
        # repeats, so a test of "it rate-limits" does not turn into "it went away".
        if self.script:
            step = self.script.pop(0)
            self.last = step if isinstance(step, str) and step in FAULTS else "unavailable"
        else:
            step = self.last
        if step == "guardrail":
            raise GuardrailBlocked("the deployment's content filter blocked the input")
        if step == "rate_limit":
            raise TransientPortError("rate limited", retry_after_s=0.5)
        if step == "auth":
            raise AuthPortError("the Foundry project refused this identity", status=401)
        if step == "unavailable":
            raise ModuleUnavailable("model_gateway")
        status, reason, text = "completed", None, step
        if isinstance(step, tuple):
            status, reason, text = step
        if isinstance(text, dict):
            text = json.dumps(text)
        return ProviderReply(
            text=text,
            status=status,
            incomplete_reason=reason,
            input_tokens=len(user_input) // 4 + 1 if self.report_usage else None,
            output_tokens=len(text) // 4 + 1 if self.report_usage else None,
            model=DEPLOYMENT,
            sent=sent,
        )


def deployed_for(
    prompt: RenderedPrompt, script: list[Any] | None = None
) -> tuple[FoundryAgentServiceGateway, StubAgentService]:
    """A gateway whose lock and Foundry agree with `prompt`, as after a clean agents-sync."""
    stub = StubAgentService(script=list(script or []))
    system = prompt.messages[0].content
    name = "wd-" + prompt.name.replace("_", "-")
    stub.versions[(name, "1")] = DeployedVersion(DEPLOYMENT, system)
    lock = AgentLock(
        agents={
            prompt.name: AgentLockEntry(
                agent_name=name,
                version="1",
                prompt_name=prompt.name,
                prompt_version=prompt.version,
                prompt_hash=str(prompt.hash),
                definition_hash=definition_hash(model=DEPLOYMENT, instructions=system),
                deployment=DEPLOYMENT,
                synced_at=datetime(2026, 11, 4, tzinfo=UTC),
            )
        }
    )
    clock = [0.0]
    gateway = FoundryAgentServiceGateway(
        client=stub,
        lock=lock,
        price=PRICE,
        sleep=lambda s: clock.__setitem__(0, clock[0] + s),
        monotonic=lambda: clock[0],
    )
    return gateway, stub
