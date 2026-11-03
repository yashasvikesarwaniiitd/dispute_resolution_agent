"""The DomainPack port (kernel-interfaces.md section 6.3).

Everything the kernel does not know about disputes lives behind this. The kernel owns cases,
states, policy and execution; a pack owns what a *weight* dispute means — how a charge is
calculated, what evidence is needed, which actions are on the menu.

Every method is pure. That is what makes `assess` checkable: the same evidence bundle must
give the same assessment, because the numbers it produces end up in a message to a seller
(I9). A pack that read a clock or a database could not promise that.

One boundary worth stating. `allowed_actions` returns the pack's menu, and the kernel then
removes `propose_credit` and `uphold_charge` when the case has a live or verified credit
(orchestrator.md 3.6). The pack cannot do that subtraction itself: credits on *other* cases
for the same shipment are not its business, and a pack that tried to look would need a store.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import CaseState, ProposalAction
from dispute_agent.kernel.domain.evidence import (
    ChargeAssessment,
    EligibilityResult,
    EvidenceBundle,
    EvidenceRequirement,
)
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.json_values import JsonScalar, JsonValue
from dispute_agent.kernel.domain.reasoning import (
    CaseBriefing,
    DraftContext,
    DraftedMessage,
    Proposal,
)


class DomainPack(Protocol):
    name: str
    version: str

    def parse_facts(self, raw: Mapping[str, JsonValue]) -> Mapping[str, JsonScalar]:
        """Validate `pack_facts` at intake. Raises DomainValidationError.

        Flat scalars only, so the kernel stays domain-neutral and the pack owns the meaning.
        """
        ...

    def eligibility(self, facts: CaseFacts) -> EligibilityResult:
        """Pure. `unknown` when the facts are insufficient.

        Not a guess: an unknown eligibility escalates with `eligibility_unknown`, because a
        dispute refused on a shipment status nobody could confirm is a dispute refused for
        the wrong reason.
        """
        ...

    def evidence_plan(self, facts: CaseFacts) -> Sequence[EvidenceRequirement]:
        """Pure, non-empty. Each requirement names a registered source."""
        ...

    def assess(
        self, case_id: CaseId, gather_round: int, bundle: EvidenceBundle
    ) -> ChargeAssessment:
        """Pure and deterministic (I9): equal bundles give equal assessments.

        Never raises for missing evidence — it reports it. An assessment that threw on an
        incomplete bundle would turn "we do not know yet" into a crash.
        """
        ...

    def allowed_actions(
        self, state: CaseState, assessment: ChargeAssessment
    ) -> Sequence[ProposalAction]:
        """Pure. Always includes ESCALATE_TO_HUMAN, so there is never an empty menu."""
        ...

    def baseline_proposal(self, briefing: CaseBriefing) -> Proposal:
        """The rules-only reasoner (FLOW section 5).

        Decides clear cases and escalates otherwise. It is both the `rules_only` adapter and
        the fallback when the model is unavailable, and ADR 0024 makes it the bar the AI has
        to beat rather than a degraded mode.
        """
        ...

    def render_template(self, ctx: DraftContext) -> DraftedMessage:
        """Deterministic message text for the rules-only path."""
        ...

    def policy_defaults(self) -> Mapping[str, JsonValue]:
        """Pack-specific defaults the policy config may override."""
        ...
