"""R03 module_available (mandatory). I6.

The module an action needs must be on. Naming the first missing one in the reason is what
makes the review queue actionable: `module_unavailable:email` tells a person what to switch
back on, where a bare "unavailable" sends them looking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext

#: Which module each action type needs. A new action type with no entry is a programming
#: error, not a reason to allow it, so `evaluate` denies rather than passing it through.
MODULE_FOR: dict[ActionType, str] = {
    ActionType.SEND_MESSAGE: "email",
    ActionType.UPSERT_ISSUE: "ticketing",
    ActionType.POST_CREDIT: "ledger",
}


class ModuleAvailable:
    """Implements PolicyRule."""

    rule_id = "R03"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if ctx.intent is None:
            return allow(self.rule_id, "no_intent")

        needed = MODULE_FOR.get(ctx.intent.action_type)
        if needed is None:
            return deny(self.rule_id, f"unmapped_action_type:{ctx.intent.action_type.value}")
        if not ctx.module_enabled(needed):
            return deny(self.rule_id, f"module_unavailable:{needed}")

        # A credit also needs somewhere to request approval from; without it the case would
        # wait for a review nobody will ever be asked for (I3).
        if ctx.intent.action_type is ActionType.POST_CREDIT and not ctx.module_enabled("approvals"):
            return deny(self.rule_id, "module_unavailable:approvals")
        return allow(self.rule_id)
