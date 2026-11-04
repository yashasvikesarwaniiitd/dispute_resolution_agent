"""R13 credit_requires_approval (mandatory). I3.

At planning a credit always needs approval — there is no amount small enough to skip it
(D30: no caps, approval is the gate). At execution the approval must cover this exact credit:
same intent, same payload hash, same amount, APPROVED by a human, unused, unexpired.

The amount is checked as well as the hash because an approval is a person agreeing to a
number. If the amount could change after the yes, the record would say a human authorised
something they never saw.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import (
    ActionType,
    ApprovalDecision,
    ApprovalSubject,
    PolicyStage,
)
from dispute_agent.kernel.policy.verdicts import allow, deny, require_approval

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class CreditRequiresApproval:
    """Implements PolicyRule."""

    rule_id = "R13"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.POST_CREDIT:
            return allow(self.rule_id, "not_a_credit")

        if ctx.stage is PolicyStage.PLANNING:
            return require_approval(self.rule_id, "credit_requires_approval")

        approval = ctx.approval
        if approval is None:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.intent_id != intent.id:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.payload_hash != intent.payload_hash:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.subject is not ApprovalSubject.CREDIT:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.decision is not ApprovalDecision.APPROVED:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.decided_by is None or not approval.decided_by.id.startswith("human:"):
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.decided_by.id not in ctx.policy.approvals.approver_ids:
            # T09 at the executor's own re-check, not only where the decision is applied.
            return deny(self.rule_id, "approver_not_listed")
        if approval.used_by_action_id is not None:
            return deny(self.rule_id, "approval_missing_or_unbound")

        amount = getattr(intent.payload, "amount", None)
        if amount is None or approval.request.amount != amount:
            return deny(self.rule_id, "credit_amount_mismatch")
        if approval.request.expires_at <= ctx.now:
            return deny(self.rule_id, "approval_stale")
        return allow(self.rule_id)
