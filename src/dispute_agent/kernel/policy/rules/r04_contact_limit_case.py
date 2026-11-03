"""R04 contact_limit_case. I13.

A seller gets at most `contact.max_messages_per_case` messages about one dispute. The limit
exists because an agent that can write without bound is an agent that can harass someone by
accident, and the failure would look like normal operation from the inside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class ContactLimitCase:
    """Implements PolicyRule."""

    rule_id = "R04"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if ctx.intent is None or ctx.intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")
        limit = ctx.policy.contact.max_messages_per_case
        if ctx.messages_sent_total >= limit:
            return deny(self.rule_id, "contact_limit_case")
        return allow(self.rule_id)
