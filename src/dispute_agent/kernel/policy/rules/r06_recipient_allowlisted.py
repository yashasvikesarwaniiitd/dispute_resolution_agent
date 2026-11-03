"""R06 recipient_allowlisted. I11.

The second place this is checked. Intake refuses a dispute naming an unlisted seller; this
refuses an outbound message to one, which catches the case where a message's recipient came
from somewhere other than the case facts.

That is also why it compares against `case.facts.seller_contact` as well as the allowlist: a
recipient the system invented is a recipient nobody authorised, however allowlisted it is
(R07 covers the mismatch in full).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class RecipientAllowlisted:
    """Implements PolicyRule."""

    rule_id = "R06"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")

        recipient = getattr(intent.payload, "to", "")
        allowed = {entry.lower() for entry in ctx.policy.messages.allowed_recipients}
        if recipient.lower() not in allowed:
            return deny(self.rule_id, "recipient_not_allowlisted")
        if recipient.lower() != ctx.case.facts.seller_contact.lower():
            return deny(self.rule_id, "recipient_mismatch")
        return allow(self.rule_id)
