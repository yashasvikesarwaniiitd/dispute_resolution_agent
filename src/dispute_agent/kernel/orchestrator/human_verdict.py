"""A person decides a case that is with them (ADR 0050, T49-T50).

Run by dispatch for a HUMAN_DECIDED_CASE. The person chooses from code's menu - uphold the
charge, credit the code-proven amount, ask the hub to remeasure, ask the seller for evidence -
and that choice is recorded as a proposal under their name and acted on by the investigate
step's own code: the credit amount from the assessment (I9), the pack's draft, the same policy,
and an approval of the exact amount or text before anything is sent (I3, I13).

- RequireApproval -> T49: the credit or message waits in the review queue.
- Allow -> T50: a message or a remeasurement ticket is planned (never a credit, C7).
- Anything else -> rest, with HUMAN_DECISION_REFUSED on the trail: the case stays with the
  person, who reads why. A refusal never sends the case anywhere the person did not choose.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActorKind, InternalEventType, Trigger
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.ids import ProposalId
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.inbound import HumanDecidedCase
    from dispute_agent.kernel.domain.models import Actor, Case
    from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
    from dispute_agent.kernel.ports import IdGenerator

#: The outcomes that move the case (T49, T50). Anything else the person is told about.
_MOVES = (Trigger.VERDICT_ALLOW, Trigger.VERDICT_REQUIRE_APPROVAL)


@dataclass
class HumanVerdictStep:
    """Not frozen, like every step (the Step protocol's `name`); bound copies come from `replace`."""

    investigate: InvestigateStep
    ids: IdGenerator
    choice: HumanDecidedCase | None = None
    actor: Actor | None = None
    name: str = "human_verdict"

    def with_decision(self, choice: HumanDecidedCase, actor: Actor) -> HumanVerdictStep:
        """This step, for one person's decision. The registered step carries none."""
        return replace(self, choice=choice, actor=actor)

    def run(self, case: Case, now: datetime) -> StepOutcome:
        if self.choice is None or self.actor is None or self.actor.kind is not ActorKind.HUMAN:
            return _refused("there was no person's decision to act on")
        try:
            outcome = self.investigate.act_for_person(
                case, self.choice, self.actor, ProposalId(self.ids.new_id("prp")), now
            )
        except ModuleUnavailable as off:
            # Already with a person, so not an escalation: the person is told what is off (I6).
            return _refused(f"module_unavailable:{off.module}: it is switched off")
        if outcome.trigger in _MOVES:
            return replace(outcome, guards={**outcome.guards, "actor_is_human": True})
        reason = outcome.review_reason.detail if outcome.review_reason else outcome.detail
        return _refused(reason or "it could not be carried out")


def _refused(why: str) -> StepOutcome:
    """Rest: the case stays with the person, and the trail says why (never outside text, I5)."""
    return StepOutcome(
        detail=why[:300], notes=((InternalEventType.HUMAN_DECISION_REFUSED, why[:300]),)
    )


__all__ = ["HumanVerdictStep"]
