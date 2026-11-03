"""R01 action_on_menu.

The orchestrator already treats an action outside the briefing's menu as
`invalid_model_output`; this is the policy-side record, so the verdict table shows why the
action was refused rather than only the step's reaction to it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class ActionOnMenu:
    """Implements PolicyRule."""

    rule_id = "R01"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        proposal = ctx.proposal
        if proposal is None:
            return allow(self.rule_id, "no_proposal")
        allowed = ctx.allowed_actions
        if allowed is None:
            # No menu supplied. Inventing one here would make the rule lie; the investigate
            # step always supplies it, so this branch means a caller that is not deciding a
            # proposal, not a gap in the check.
            return allow(self.rule_id, "no_menu_supplied")
        if proposal.action not in allowed:
            return deny(self.rule_id, "action_not_allowed")
        return allow(self.rule_id)
