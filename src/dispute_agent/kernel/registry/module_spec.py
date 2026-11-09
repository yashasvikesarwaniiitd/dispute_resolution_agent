"""What modules exist, and what each one needs (ARCHITECTURE.md section 5, config.md 2).

One deviation from the ARCHITECTURE sketch, and it is forced. That sketch gives ModuleSpec
an `adapters: Mapping[str, Callable[..., object]]` of factories, but the factories live in
`modules/` and the kernel may not import them — "Kernel is pure" is an import-linter contract
and section 4 says its own signatures are a sketch. So this file declares adapters by name
with the facts validation needs, and the composition root (P0-08) maps those names to
factories. The kernel decides whether a combination is safe; it never constructs an adapter.

Dependencies hang off the adapter rather than the module because that is where they really
live: `reasoning` needs `model_gateway` only when it is running `llm`, and `rules_only` needs
nothing at all (config.md section 2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from dispute_agent.kernel.ports import (
    ApprovalService,
    EvidenceSource,
    ImageChecker,
    InputScreen,
    Ledger,
    MessageChannel,
    ModelGateway,
    PatternContextProvider,
    Reasoner,
    TicketingSystem,
    Tracer,
)

#: How a module is wired when it is switched off. "disabled" is an adapter whose every method
#: raises ModuleUnavailable (I6). The two exceptions are not inconsistencies — see
#: orchestrator.md section 5, and the docstrings in modules/cross_case/null and
#: modules/tracing/noop for why absence means something different for those two.
OffAdapter = Literal["disabled", "null", "noop"]


@dataclass(frozen=True)
class AdapterSpec:
    """One way of implementing a module."""

    name: str
    depends_on: tuple[str, ...] = ()
    preview: bool = False
    #: Environment variable NAMES this adapter needs. Names only, never values (I12).
    #: Checking they are present belongs to the phase that builds the adapter.
    needs_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModuleSpec:
    name: str
    port: type
    adapters: dict[str, AdapterSpec] = field(default_factory=dict)
    #: A required module cannot be disabled. None are today: S05b, S20 and S41 all run with a
    #: module switched off, and the system is meant to escalate rather than refuse to start.
    required: bool = False
    off_adapter: OffAdapter = "disabled"

    def adapter(self, name: str) -> AdapterSpec | None:
        return self.adapters.get(name)


def _adapters(*specs: AdapterSpec) -> dict[str, AdapterSpec]:
    return {spec.name: spec for spec in specs}


#: The catalogue. config.md section 2 is the authority; test_validation.py checks the two
#: agree on module names, adapter names and dependencies.
MODULE_SPECS: dict[str, ModuleSpec] = {
    "evidence": ModuleSpec(
        name="evidence",
        port=EvidenceSource,
        adapters=_adapters(AdapterSpec("synthetic_shipments")),
    ),
    "model_gateway": ModuleSpec(
        name="model_gateway",
        port=ModelGateway,
        adapters=_adapters(
            AdapterSpec("fake"),
            AdapterSpec("anthropic_direct", needs_env=("ANTHROPIC_API_KEY",)),
            AdapterSpec("foundry_agent_service"),
            AdapterSpec("foundry_openai"),
        ),
    ),
    "reasoning": ModuleSpec(
        name="reasoning",
        port=Reasoner,
        adapters=_adapters(
            AdapterSpec("rules_only"),
            AdapterSpec("llm", depends_on=("model_gateway",)),
        ),
    ),
    "input_screen": ModuleSpec(
        name="input_screen",
        port=InputScreen,
        adapters=_adapters(
            AdapterSpec("rules_based"),
            AdapterSpec("foundry_guardrails", depends_on=("model_gateway",)),
        ),
    ),
    "tracing": ModuleSpec(
        name="tracing",
        port=Tracer,
        adapters=_adapters(
            AdapterSpec("noop"),
            AdapterSpec("otel_foundry", preview=True),
        ),
        off_adapter="noop",
    ),
    "email": ModuleSpec(
        name="email",
        port=MessageChannel,
        adapters=_adapters(
            AdapterSpec("fake"),
            AdapterSpec("outlook_graph"),
            AdapterSpec("gmail_api"),  # ADR 0038: a personal Gmail as the pilot mailbox
        ),
    ),
    "ticketing": ModuleSpec(
        name="ticketing",
        port=TicketingSystem,
        adapters=_adapters(
            AdapterSpec("fake"),
            AdapterSpec("jira_cloud"),
        ),
    ),
    "ledger": ModuleSpec(
        name="ledger",
        port=Ledger,
        adapters=_adapters(AdapterSpec("simulated", depends_on=("approvals",))),
    ),
    "approvals": ModuleSpec(
        name="approvals",
        port=ApprovalService,
        adapters=_adapters(AdapterSpec("store_backed")),
    ),
    "cross_case": ModuleSpec(
        name="cross_case",
        port=PatternContextProvider,
        adapters=_adapters(
            AdapterSpec("null"),
            AdapterSpec("sql_context", depends_on=("ticketing",)),
        ),
        off_adapter="null",
    ),
    "image_check": ModuleSpec(
        name="image_check",
        port=ImageChecker,
        adapters=_adapters(
            # ADR 0049: Foundry gpt-5-mini reads the photo through its own guarded gateway.
            AdapterSpec(
                "foundry_vision",
                needs_env=(
                    "FOUNDRY_PROJECT_ENDPOINT",
                    "FOUNDRY_MODEL_DEPLOYMENT",
                    "AZURE_TENANT_ID",
                ),
            ),
            AdapterSpec("recorded"),
        ),
    ),
    "evals": ModuleSpec(
        name="evals",
        # No kernel port: evals is a test harness, not something a case ever calls.
        port=type(None),
        adapters=_adapters(AdapterSpec("default")),
    ),
}

#: config.md section 2: the secondary model route may only ever be this one.
PERMITTED_SECONDARY_ADAPTER = "anthropic_direct"
