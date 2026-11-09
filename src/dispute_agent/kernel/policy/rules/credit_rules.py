"""R14, R15, R16, R18 and R25: the money rules (policy-rules.md, money-rules.md, I3, I9).

Together these say what a credit must be before a person is even asked to approve it, and
again before it is posted: for a reason code proved, for exactly the amount code derived,
no more than is still owed, on a case that has not already been credited, under an approval
that is still fresh. There are no amount caps (D30) - a person approves every credit, and
the amount is exactly what code computed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionType, ApprovalDecision, MessageKind
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.credit import PROVEN_REASONS, credit_amount
from dispute_agent.kernel.policy.verdicts import allow, deny, escalate

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import CreditPayload, RuleResult
    from dispute_agent.kernel.policy.verdicts import PolicyContext


def _credit(ctx: PolicyContext) -> CreditPayload | None:
    intent = ctx.intent
    if intent is None or intent.action_type is not ActionType.POST_CREDIT:
        return None
    payload = intent.payload
    return payload if getattr(payload, "type", None) == "credit" else None  # type: ignore[return-value]


class CreditReasonProven:
    """R14. The reason must be a configured, code-proven one - and the only one."""

    rule_id = "R14"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        credit = _credit(ctx)
        if credit is None:
            return allow(self.rule_id, "not_a_credit")
        assessment = ctx.assessment
        if assessment is None:
            return deny(self.rule_id, "credit_reason_unproven: no assessment")
        configured = set(ctx.policy.credits.reason_codes) & set(PROVEN_REASONS)
        if credit.reason_code not in configured:
            return deny(self.rule_id, f"credit_reason_unproven: {credit.reason_code!r} unknown")
        chosen_by_a_person = ctx.proposal is not None and ctx.proposal.reasoner == "human"
        if len(assessment.definite_errors) > 1 and not chosen_by_a_person:
            # One credit per case (money-rules.md section 4): two proven errors are a
            # person's call, not a choice between two amounts. A person who chose one of
            # them has made that call (ADR 0050); the amount is still code's, the reason
            # must still be proven, and the credit still waits for an approval.
            return escalate(self.rule_id, "multiple_definite_errors")
        if credit.reason_code not in assessment.definite_errors:
            return deny(self.rule_id, "credit_reason_unproven: not in definite_errors")
        return allow(self.rule_id)


class CreditAmountDeterministic:
    """R15. The amount is exactly what code derives - never a model's figure (I9)."""

    rule_id = "R15"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        credit = _credit(ctx)
        if credit is None:
            return allow(self.rule_id, "not_a_credit")
        if ctx.assessment is None or ctx.credited_on_shipment is None:
            return deny(self.rule_id, "credit_amount_mismatch: inputs unknown")
        expected = credit_amount(
            ctx.assessment,
            credit.reason_code,
            already_credited=_minus_self(ctx.credited_on_shipment, ctx),
            already_credited_duplicate=_minus_self(
                ctx.credited_duplicate_on_shipment or Money.of("0"), ctx, duplicate=True
            ),
        )
        if expected is None or expected.to_paise() != credit.amount.to_paise():
            return deny(
                self.rule_id,
                f"credit_amount_mismatch: intent {credit.amount}, derived "
                f"{expected if expected is not None else 'nothing owed'}",
            )
        return allow(self.rule_id)


class CreditWithinOutstanding:
    """R16. Never more than is still owed on the charge line (the structural limit, D30)."""

    rule_id = "R16"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        credit = _credit(ctx)
        if credit is None:
            return allow(self.rule_id, "not_a_credit")
        if ctx.debited_on_shipment is None or ctx.credited_on_shipment is None:
            return escalate(self.rule_id, "ledger_state_unknown")
        others = _minus_self(ctx.credited_on_shipment, ctx)
        outstanding = ctx.debited_on_shipment.to_paise() - others.to_paise()
        if credit.amount.to_paise() > outstanding:
            return deny(self.rule_id, "credit_exceeds_outstanding")
        return allow(self.rule_id)


class NoSecondOutcomeAfterCredit:
    """R18. A case that has credited may not credit again, nor uphold (ADR 0012)."""

    rule_id = "R18"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        intent = ctx.intent
        if intent is None:
            return allow(self.rule_id, "no_intent")
        is_credit = intent.action_type is ActionType.POST_CREDIT
        is_upheld = getattr(intent.payload, "kind", None) is MessageKind.UPHELD_EXPLANATION
        if (is_credit or is_upheld) and ctx.case_has_verified_credit:
            return deny(self.rule_id, "credit_already_posted")
        return allow(self.rule_id)


class ApprovalFresh:
    """R25. At execution, an approval must be decided, unexpired and unused."""

    rule_id = "R25"

    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        approval = ctx.approval
        if approval is None:
            return allow(self.rule_id, "no_approval")
        if approval.decision in (ApprovalDecision.EXPIRED, ApprovalDecision.SUPERSEDED):
            return deny(self.rule_id, "approval_stale")
        if approval.used_by_action_id is not None:
            return deny(self.rule_id, "approval_stale: already used")
        decided = approval.decided_at
        if decided is not None and decided > approval.request.expires_at:
            return deny(self.rule_id, "approval_stale: decided after expiry")
        return allow(self.rule_id)


def _minus_self(credited: Money, ctx: PolicyContext, *, duplicate: bool = False) -> Money:
    """Credited on the shipment, excluding this intent's own planned credit.

    At execution the action row for this very credit is PLANNED, so it is already in the
    store's sum. Counting it as "already credited" would make every credit refuse itself.
    """
    credit = _credit(ctx)
    if credit is None or not ctx.intent_counted_in_store:
        return credited
    if duplicate != (credit.reason_code == "duplicate_charge"):
        return credited
    return Money.from_paise(max(0, credited.to_paise() - credit.amount.to_paise()))


__all__ = [
    "ApprovalFresh",
    "CreditAmountDeterministic",
    "CreditReasonProven",
    "CreditWithinOutstanding",
    "NoSecondOutcomeAfterCredit",
]
