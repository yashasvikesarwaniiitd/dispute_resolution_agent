"""R08 message_review_required. I13.

In the pilot every message kind is reviewed (D6), so this returns REQUIRE_APPROVAL for all of
them. It reads the configured sets rather than hard-coding that, because the interesting case
is the one where somebody later moves a kind to `auto_send_kinds` — and then this rule is
the record of which kinds a human agreed could go without being read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.verdicts import allow, require_approval

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class MessageReviewRequired:
    """Implements PolicyRule."""

    rule_id = "R08"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")
        kind = getattr(intent.payload, "kind", None)
        if kind is None:
            return allow(self.rule_id, "no_kind")
        if kind in ctx.policy.messages.review_required_kinds:
            return require_approval(self.rule_id, "message_review_required")
        return allow(self.rule_id, f"auto_send:{kind.value}")
