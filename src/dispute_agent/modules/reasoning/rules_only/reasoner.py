"""The rules-only reasoner (P2-08, agent-design.md section 5, ADR 0024).

A `Reasoner` that is not a model. It asks the pack's decision table and returns what it
says, which makes it two things at once: the adapter you run when you want the system to
behave deterministically, and the fallback the orchestrator falls back *to* when the model
is unavailable.

ADR 0024 is what makes that second role worth taking seriously. The baseline is not a
degraded mode to be tolerated — it is the bar the AI is measured against, per round and on
the proposal action. A release gate compares the two. So this adapter existing and being
exercised is how "the AI is better than rules" stops being an assertion.

It holds an `IdGenerator` and a `Clock` because a `Proposal` needs an id and a timestamp and
the pack has neither: a pack that minted its own ids would not be reproducible, and
reproducibility is the only property the baseline really has.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.ids import ProposalId
    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.kernel.ports import Clock, IdGenerator

MODULE = "reasoning"
ADAPTER = "rules_only"


class BaselinePack(Protocol):
    """The one thing this adapter asks a pack for.

    Narrower than `DomainPack`, for the same reason the orchestrator steps take a narrow
    `CasePack`: an adapter typed against the whole port could reach `assess` and decide to
    recompute the numbers itself, and "the reasoner does not calculate" would become a
    convention rather than something the type says.

    It also reflects where the pack actually is. `WeightDisputePack` does not yet satisfy
    `DomainPack` — `render_template` is Phase 3 — and pretending otherwise with a cast
    would hide that from the next person to read it.
    """

    def baseline_proposal(
        self,
        briefing: CaseBriefing,
        *,
        proposal_id: ProposalId,
        now: datetime,
        fallback_used: bool = False,
    ) -> Proposal: ...


@dataclass
class RulesOnlyReasoner:
    """Implements Reasoner. Never calls a model, so I14 holds by having nothing to route."""

    pack: BaselinePack
    ids: IdGenerator
    clock: Clock
    #: Set when this stood in for a model that was unavailable, so the proposal says which
    #: it was. `orchestrator.md` section 5 records `module_unavailable:<m>` separately on the
    #: REASONER_FALLBACK event; this is the flag on the proposal itself.
    fallback_used: bool = False

    def propose(self, briefing: CaseBriefing) -> Proposal:
        """The table's answer, as a Proposal. No I/O, no judgement of free text."""
        from dispute_agent.kernel.domain.ids import ProposalId

        return self.pack.baseline_proposal(
            briefing,
            proposal_id=ProposalId(self.ids.new_id("prp")),
            now=self.clock.now(),
            fallback_used=self.fallback_used,
        )


__all__ = ["ADAPTER", "MODULE", "BaselinePack", "RulesOnlyReasoner"]
