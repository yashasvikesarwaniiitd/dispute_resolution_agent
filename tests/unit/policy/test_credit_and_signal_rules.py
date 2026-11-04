"""R14-R16, R18, R20-R23, R25 (P5-06, P4-09): each fires where it should, and only there."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from dispute_agent.kernel.domain.actions import CreditPayload
from dispute_agent.kernel.domain.enums import (
    ApprovalDecision,
    Confidence,
    FindingKind,
    MessageKind,
    ProposalAction,
    ScreenKind,
    VerdictKind,
)
from dispute_agent.kernel.domain.evidence import ChargeAssessment
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, AssessmentId, ProposalId
from dispute_agent.kernel.domain.reasoning import (
    ActionParameters,
    Finding,
    PatternInfluence,
    Proposal,
    ReplyReading,
    ScreenResult,
)
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.credit import credit_amount
from dispute_agent.kernel.policy.rules.credit_rules import (
    ApprovalFresh,
    CreditAmountDeterministic,
    CreditReasonProven,
    CreditWithinOutstanding,
    NoSecondOutcomeAfterCredit,
)
from dispute_agent.kernel.policy.rules.model_signal_rules import (
    GuardrailBlockedRule,
    LowConfidenceConsequential,
    ManipulationFlagged,
    WithdrawalClaimed,
)
from tests.unit.policy.test_rules import (
    CASE,
    LATER,
    NOW,
    an_approval,
    credit_intent,
    ctx,
    message_intent,
)

ZERO = Money.of("0")


def assessment(
    *,
    charged: str = "2000.00",
    recalculated: str = "180.00",
    errors: tuple[str, ...] = ("calculation_error",),
    duplicate_total: str | None = None,
) -> ChargeAssessment:
    c, r = Money.of(charged), Money.of(recalculated)
    return ChargeAssessment(
        id=AssessmentId("asm_1"),
        case_id=CASE.id,
        gather_round=1,
        pack_version="1",
        eligible="eligible",
        charged=c,
        recalculated=r,
        discrepancy=c - r,
        duplicate_debit_total=Money.of(duplicate_total) if duplicate_total else None,
        definite_errors=errors,
        input_hash=payload_hash("in"),
    )


def credit_ctx(amount: str = "1820.00", reason: str = "calculation_error", **over: Any) -> Any:
    intent = credit_intent(amount)
    if reason != "calculation_error":
        intent = intent.model_copy(
            update={
                "payload": CreditPayload(
                    shipment_ref="SHP-0001", amount=Money.of(amount), reason_code=reason
                )
            }
        )
    fields: dict[str, Any] = {
        "intent": intent,
        "assessment": assessment(),
        "credited_on_shipment": ZERO,
        "credited_duplicate_on_shipment": ZERO,
        "debited_on_shipment": Money.of("2000.00"),
    }
    fields.update(over)
    return ctx(**fields)


def verdict(rule: Any, context: Any) -> VerdictKind:
    result: VerdictKind = rule.evaluate(context).verdict
    return result


class TestTheAmountIsDerived:
    def test_charged_minus_correct(self) -> None:
        assert credit_amount(assessment(), "calculation_error", ZERO, ZERO) == Money.of("1820.00")

    def test_earlier_credits_are_netted_so_nothing_pays_twice(self) -> None:
        earlier = Money.of("1000.00")
        assert credit_amount(assessment(), "calculation_error", earlier, ZERO) == Money.of("820.00")

    def test_nothing_owed_is_no_credit(self) -> None:
        assert credit_amount(assessment(), "calculation_error", Money.of("1820.00"), ZERO) is None

    def test_an_unproven_reason_is_no_credit(self) -> None:
        assert credit_amount(assessment(), "remeasurement_lower", ZERO, ZERO) is None


class TestR14ReasonProven:
    def test_a_proven_reason_passes(self) -> None:
        assert verdict(CreditReasonProven(), credit_ctx()) is VerdictKind.ALLOW

    def test_a_reason_the_assessment_does_not_prove_is_denied(self) -> None:
        context = credit_ctx(reason="remeasurement_lower")
        assert verdict(CreditReasonProven(), context) is VerdictKind.DENY

    def test_two_definite_errors_escalate(self) -> None:
        """Scenario S38: one credit per case; two proven errors are a person's call."""
        both = assessment(errors=("calculation_error", "duplicate_charge"))
        result = CreditReasonProven().evaluate(credit_ctx(assessment=both))
        assert result.verdict is VerdictKind.ESCALATE
        assert result.reason == "multiple_definite_errors"


class TestR15AmountDeterministic:
    def test_the_exact_amount_passes(self) -> None:
        assert verdict(CreditAmountDeterministic(), credit_ctx()) is VerdictKind.ALLOW

    def test_one_paisa_off_is_denied(self) -> None:
        assert verdict(CreditAmountDeterministic(), credit_ctx("1820.01")) is VerdictKind.DENY

    def test_unknown_credits_on_the_shipment_refuse(self) -> None:
        context = credit_ctx(credited_on_shipment=None)
        assert verdict(CreditAmountDeterministic(), context) is VerdictKind.DENY

    def test_at_execution_the_intent_s_own_row_is_not_counted_against_it(self) -> None:
        context = credit_ctx(credited_on_shipment=Money.of("1820.00"), intent_counted_in_store=True)
        assert verdict(CreditAmountDeterministic(), context) is VerdictKind.ALLOW

    @pytest.mark.parametrize("amount", ["150.00", "1820.00"])
    def test_no_amount_caps(self, amount: str) -> None:
        """D30, S39 versus S01: a large credit takes exactly the small one's path."""
        small = assessment(charged="330.00", recalculated="180.00")
        a = small if amount == "150.00" else assessment()
        assert verdict(CreditAmountDeterministic(), credit_ctx(amount, assessment=a)) is (
            VerdictKind.ALLOW
        )


class TestR16WithinOutstanding:
    def test_within_what_is_owed_passes(self) -> None:
        assert verdict(CreditWithinOutstanding(), credit_ctx()) is VerdictKind.ALLOW

    def test_more_than_is_owed_is_denied(self) -> None:
        context = credit_ctx(credited_on_shipment=Money.of("500.00"))
        assert verdict(CreditWithinOutstanding(), context) is VerdictKind.DENY

    def test_an_unreadable_ledger_escalates(self) -> None:
        result = CreditWithinOutstanding().evaluate(credit_ctx(debited_on_shipment=None))
        assert result.reason == "ledger_state_unknown"


class TestR18NoSecondOutcome:
    def test_a_credited_case_cannot_credit_again(self) -> None:
        context = credit_ctx(case_has_verified_credit=True)
        assert verdict(NoSecondOutcomeAfterCredit(), context) is VerdictKind.DENY

    def test_a_credited_case_cannot_uphold(self) -> None:
        upheld = message_intent().model_copy(
            update={
                "payload": message_intent().payload.model_copy(
                    update={"kind": MessageKind.UPHELD_EXPLANATION}
                )
            }
        )
        context = ctx(intent=upheld, case_has_verified_credit=True)
        assert verdict(NoSecondOutcomeAfterCredit(), context) is VerdictKind.DENY

    def test_an_uncredited_case_may(self) -> None:
        assert verdict(NoSecondOutcomeAfterCredit(), credit_ctx()) is VerdictKind.ALLOW


class TestR25ApprovalFresh:
    def test_a_fresh_approval_passes(self) -> None:
        assert verdict(ApprovalFresh(), ctx(approval=an_approval())) is VerdictKind.ALLOW

    @pytest.mark.parametrize(
        "stale",
        [
            {"decision": ApprovalDecision.EXPIRED, "decided_by": None, "decided_at": None},
            {"used_by_action_id": ActionId("act_other")},
            {"decided_at": LATER + timedelta(minutes=1)},
        ],
        ids=["expired", "already used", "decided after expiry"],
    )
    def test_a_stale_approval_is_denied(self, stale: dict[str, Any]) -> None:
        assert verdict(ApprovalFresh(), ctx(approval=an_approval(**stale))) is VerdictKind.DENY


def a_proposal(action: ProposalAction, confidence: Confidence, flags: tuple[str, ...] = ()) -> Any:
    return Proposal(
        id=ProposalId("prp_1"),
        case_id=CASE.id,
        gather_round=1,
        action=action,
        confidence=confidence,
        rationale="r",
        findings=(Finding(kind=FindingKind.VERIFIED_FACT, statement="s"),),
        action_parameters=ActionParameters(),
        pattern_influence=PatternInfluence(considered=False, changed_recommendation=False),
        manipulation_flags=flags,
        reasoner="llm",
        briefing_hash=payload_hash("b"),
        created_at=NOW,
    )


def a_reading(intent: str, flags: tuple[str, ...] = ()) -> ReplyReading:
    return ReplyReading(
        intent=intent,
        manipulation_flags=flags,
        summary="s",
        language="en",
        prompt_name="seller_reply_interpreter",
        prompt_version="v1",
        prompt_hash=payload_hash("p"),
    )


class TestModelSignals:
    def test_r20_a_claimed_withdrawal_goes_to_a_person(self) -> None:
        result = WithdrawalClaimed().evaluate(ctx(reply_readings=(a_reading("withdraws_dispute"),)))
        assert result.reason == "seller_withdrawal_claimed"

    def test_r20_and_r21_both_fire_on_a_mixed_withdrawal(self) -> None:
        """Corpus item 6: the withdrawal wins the intent; the flag still escalates."""
        mixed = ctx(
            reply_readings=(
                a_reading(
                    "withdraws_dispute",
                    ("bulk_refund",),
                ),
            )
        )
        assert verdict(WithdrawalClaimed(), mixed) is VerdictKind.ESCALATE
        assert verdict(ManipulationFlagged(), mixed) is VerdictKind.ESCALATE

    @pytest.mark.parametrize(
        "context",
        [
            pytest.param(
                {"proposal": a_proposal(ProposalAction.WAIT, Confidence.HIGH, ("x",))},
                id="reasoner flag",
            ),
            pytest.param(
                {"reply_readings": (a_reading("contains_instruction_attempt"),)},
                id="reply intent",
            ),
            pytest.param(
                {
                    "screen_results": (
                        ScreenResult(
                            kind=ScreenKind.SELLER_REPLY,
                            flags=("instruction_like",),
                            adapter="rules_based",
                        ),
                    )
                },
                id="screen flag",
            ),
        ],
    )
    def test_r21_any_manipulation_signal_escalates(self, context: dict[str, Any]) -> None:
        assert verdict(ManipulationFlagged(), ctx(**context)) is VerdictKind.ESCALATE

    def test_r21_clean_inputs_change_nothing(self) -> None:
        """I15 positive control: a clean result never relaxes and never fires."""
        clean = ctx(
            proposal=a_proposal(ProposalAction.WAIT, Confidence.HIGH),
            reply_readings=(a_reading("provides_requested_evidence"),),
            screen_results=(ScreenResult(kind=ScreenKind.SELLER_REPLY, adapter="rules_based"),),
        )
        assert verdict(ManipulationFlagged(), clean) is VerdictKind.ALLOW

    @pytest.mark.parametrize(
        ("action", "confidence", "expected"),
        [
            (ProposalAction.UPHOLD_CHARGE, Confidence.LOW, VerdictKind.ESCALATE),
            (ProposalAction.PROPOSE_CREDIT, Confidence.LOW, VerdictKind.ESCALATE),
            (ProposalAction.UPHOLD_CHARGE, Confidence.MEDIUM, VerdictKind.ALLOW),
            (ProposalAction.REQUEST_SELLER_EVIDENCE, Confidence.LOW, VerdictKind.ALLOW),
        ],
    )
    def test_r22_low_confidence_only_on_consequential_actions(
        self, action: ProposalAction, confidence: Confidence, expected: VerdictKind
    ) -> None:
        context = ctx(proposal=a_proposal(action, confidence))
        assert verdict(LowConfidenceConsequential(), context) is expected

    def test_r23_a_blocked_screen_goes_to_a_person(self) -> None:
        blocked = ScreenResult(kind=ScreenKind.SELLER_REPLY, blocked=True, adapter="foundry")
        result = GuardrailBlockedRule().evaluate(ctx(screen_results=(blocked,)))
        assert result.reason == "guardrail_blocked"
