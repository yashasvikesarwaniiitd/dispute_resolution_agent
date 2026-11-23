"""R05 contact_limit_daily.

At most `contact.max_messages_per_day` messages in a day, counted from midnight in
`contact.day_boundary_tz`. The reminder step turns this verdict into REMINDER_SKIPPED rather
than an escalation: being quiet for a day is not a problem anyone needs to look at.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType, MessageKind
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class ContactLimitDaily:
    """Implements PolicyRule."""

    rule_id = "R05"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if ctx.intent is None or ctx.intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")
        kind = getattr(ctx.intent.payload, "kind", None)
        if kind is MessageKind.CLARIFICATION_REQUEST:
            # It answers the seller's own reply, so it is not unsolicited contact; R04's
            # per-case total still bounds it (policy-rules.md R05, D19).
            return allow(self.rule_id, "clarification_exempt")
        if kind is MessageKind.CREDIT_CONFIRMATION:
            # Telling the seller their money was credited is owed, not unsolicited, and the
            # case cannot close until it is sent (ADR 0012). Found live (ADR 0054): with the
            # hub's answer read the same day as an evidence request, the credit posted and
            # this confirmation was refused, leaving a paid case with a person. R04 bounds it.
            return allow(self.rule_id, "confirmation_exempt")
        if ctx.messages_sent_today >= ctx.policy.contact.max_messages_per_day:
            return deny(self.rule_id, "contact_limit_daily")
        return allow(self.rule_id)
