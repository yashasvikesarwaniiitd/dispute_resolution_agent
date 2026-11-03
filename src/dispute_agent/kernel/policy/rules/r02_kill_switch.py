"""R02 kill_switch (mandatory). I10.

Checked here and again inside the executor. That is not belt-and-braces: policy runs at
planning, and the switch may be thrown in the seconds between planning an action and
executing it. A check only at the entry point would let an action planned before the switch
went on still reach the outside world.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class KillSwitchRule:
    """Implements PolicyRule."""

    rule_id = "R02"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        if ctx.kill_switch:
            return deny(self.rule_id, "kill_switch_active")
        return allow(self.rule_id)
