"""R17 credit_not_duplicate (mandatory). I3.

One live credit per shipment and reason, across every case — not per case. A seller who
raises the same dispute twice, or two cases for one shipment, would otherwise each be able to
produce a credit that looked legitimate in isolation.

Backed by `ux_actions_one_credit`, a partial unique index over
(credit_shipment_ref, credit_reason_code) for any status other than FAILED. Only a credit
proven not to have happened frees the slot: an UNKNOWN one might have been posted, so it
keeps holding the place until a person resolves it (I4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType
from dispute_agent.kernel.policy.verdicts import allow, deny

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class CreditNotDuplicate:
    """Implements PolicyRule."""

    rule_id = "R17"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None or intent.action_type is not ActionType.POST_CREDIT:
            return allow(self.rule_id, "not_a_credit")
        if ctx.live_credits_same_shipment_reason > 0:
            return deny(self.rule_id, "duplicate_credit")
        if ctx.case_has_verified_credit:
            # ADR 0012: a case that already credited is finished with crediting.
            return deny(self.rule_id, "credit_already_posted")
        return allow(self.rule_id)
