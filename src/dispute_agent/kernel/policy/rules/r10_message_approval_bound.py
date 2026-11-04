"""R10 message_approval_bound (mandatory). I13.

At execution, a message kind that needs review must carry an approval that actually covers
*this* text. Five conditions, and every one of them has a way of going wrong that looks fine:

- same intent — otherwise an approval for one message authorises another
- equal payload hash — otherwise the text sent is not the text approved, which is the whole
  content of I13
- APPROVED, by a human — not pending, not expired, not decided by the system
- unused — an approval is single-use, or one yes becomes two sends
- not expired at `now`

The database holds the same conditions in `tr_actions_approval_binding`, so this rule failing
open would still not produce an action row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import (
    ActionType,
    ApprovalDecision,
    ApprovalSubject,
    PolicyStage,
)
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class MessageApprovalBound:
    """Implements PolicyRule."""

    rule_id = "R10"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if ctx.stage is not PolicyStage.EXECUTION:
            return allow(self.rule_id, "planning_stage")
        if intent is None or intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")

        kind = getattr(intent.payload, "kind", None)
        if kind is not None and kind not in ctx.policy.messages.review_required_kinds:
            return allow(self.rule_id, "auto_send_kind")

        approval = ctx.approval
        if approval is None:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.intent_id != intent.id:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.payload_hash != intent.payload_hash:
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
        if approval.request.subject is not ApprovalSubject.MESSAGE:
            return deny(self.rule_id, "approval_missing_or_unbound")
        if approval.request.expires_at <= ctx.now:
            return deny(self.rule_id, "approval_stale")
        return allow(self.rule_id)
