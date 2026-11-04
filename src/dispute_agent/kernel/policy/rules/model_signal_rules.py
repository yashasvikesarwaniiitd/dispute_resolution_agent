"""R20, R21, R22, R23: what the model and the screens noticed can only add caution (I5, I15).

Each of these reads model or screen output through a named field of the policy context and
can only ESCALATE. None of them can ever ALLOW anything that another rule did not: the
lattice takes the strictest verdict, so a clean reading or a clean screen changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import Confidence, ProposalAction
from dispute_agent.kernel.policy.verdicts import allow, escalate

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class WithdrawalClaimed:
    """R20. A seller who says they withdraw is confirmed by a person, never auto-closed (D20)."""

    rule_id = "R20"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if any(r.intent == "withdraws_dispute" for r in ctx.reply_readings):
            return escalate(self.rule_id, "seller_withdrawal_claimed")
        return allow(self.rule_id)


class ManipulationFlagged:
    """R21. Any sign of an attempt to steer the system sends the case to a person."""

    rule_id = "R21"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if ctx.proposal is not None and ctx.proposal.manipulation_flags:
            return escalate(self.rule_id, "manipulation_suspected: flagged by the reasoner")
        for reading in ctx.reply_readings:
            if reading.intent == "contains_instruction_attempt" or reading.manipulation_flags:
                return escalate(self.rule_id, "manipulation_suspected: flagged in a reply")
        for result in ctx.screen_results:
            flagged = sorted(f for f in result.flags if _needs_a_person(f))
            if flagged:
                return escalate(
                    self.rule_id, f"manipulation_suspected: flagged by a screen ({flagged[0]})"
                )
        return allow(self.rule_id)


# Screen flags that send a case to a person. A screen that errored, text that was cut off and
# a reference to something that does not exist are not proof of an attack, but each means the
# text was not checked as it should have been - and an unchecked text is never trusted (I15).
# Phase 5 safety review, finding 2: only instruction_like and prompt_shield:* escalated.
_ESCALATING_FLAGS = frozenset(
    {"instruction_like", "screen_error", "truncated_input", "fabricated_reference"}
)


def _needs_a_person(flag: str) -> bool:
    return flag in _ESCALATING_FLAGS or flag.startswith("prompt_shield:")


class LowConfidenceConsequential:
    """R22. A low-confidence credit or uphold is a person's call (D31)."""

    rule_id = "R22"

    CONSEQUENTIAL = (ProposalAction.PROPOSE_CREDIT, ProposalAction.UPHOLD_CHARGE)

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        proposal = ctx.proposal
        if (
            proposal is not None
            and proposal.action in self.CONSEQUENTIAL
            and proposal.confidence is Confidence.LOW
        ):
            return escalate(self.rule_id, "low_confidence")
        return allow(self.rule_id)


class GuardrailBlockedRule:
    """R23. A provider content block anywhere in the round goes to a person (no failover)."""

    rule_id = "R23"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if any(result.blocked for result in ctx.screen_results):
            return escalate(self.rule_id, "guardrail_blocked")
        return allow(self.rule_id)


__all__ = [
    "GuardrailBlockedRule",
    "LowConfidenceConsequential",
    "ManipulationFlagged",
    "WithdrawalClaimed",
]
