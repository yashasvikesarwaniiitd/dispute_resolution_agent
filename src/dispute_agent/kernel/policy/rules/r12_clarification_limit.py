"""R12 clarification_limit (policy-rules.md, owner decision D19, I4).

After a seller replies, an unclear reply gets one clarification follow-up, then a person.
Without this an agent and a seller who keep talking past each other would loop until the
contact limit stopped them - three messages that each ask the same question again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType, MessageKind
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class ClarificationLimit:
    """Implements PolicyRule."""

    rule_id = "R12"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.SEND_MESSAGE:
            return allow(self.rule_id, "not_a_message")
        if getattr(intent.payload, "kind", None) is not MessageKind.CLARIFICATION_REQUEST:
            return allow(self.rule_id, "not_a_clarification")
        if ctx.clarifications_sent >= ctx.policy.contact.max_clarifications_per_case:
            return deny(self.rule_id, "reply_unresolved")
        return allow(self.rule_id)
