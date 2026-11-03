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
        allowed = getattr(ctx, "allowed_actions", None)
        if allowed is None:
            # Phase 2 builds the briefing that carries the menu. Until then there is nothing
            # to compare against, and inventing a menu here would make the rule lie.
            return allow(self.rule_id, "menu_not_available_until_phase_2")
        if proposal.action not in allowed:
            return deny(self.rule_id, "action_not_allowed")
        return allow(self.rule_id)
