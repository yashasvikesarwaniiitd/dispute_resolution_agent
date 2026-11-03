"""The domain validators that carry an invariant.

Section 4 of kernel-interfaces.md has no task card (see the P0-05 commit message), so these
models arrived without the "tests first" a card would have asked for. The validators worth
testing are the ones that make an unsafe state unrepresentable rather than merely unlikely:
a credit with no approval (I3), a verdict that is not the strictest of its inputs (I7, I15),
a closed case with no closing time (I8), evidence with a value it never found.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dispute_agent.kernel.domain.actions import Action, PolicyVerdict, RuleResult
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    CaseState,
    EvidenceStatus,
    PolicyStage,
    ReviewCode,
    StateCategory,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.evidence import EvidenceResult
from dispute_agent.kernel.domain.ids import (
    ActionId,
    ApprovalId,
    CaseId,
    CorrelationId,
    IdempotencyKey,
    IntentId,
    PayloadHash,
    VerdictId,
)
from dispute_agent.kernel.domain.models import Case, ReviewReason
from dispute_agent.kernel.domain.patterns import CautionSignal

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
HASH = PayloadHash("sha256:" + "0" * 64)

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)


def a_case(**overrides: object) -> Case:
    fields: dict[str, object] = {
        "id": CaseId("case_01HQ"),
        "pack": "weight_dispute",
        "facts": FACTS,
        "state": CaseState.INVESTIGATING,
        "version": 1,
        "correlation_id": CorrelationId("corr_01HQ"),
        "opened_at": NOW,
        "updated_at": NOW,
        "state_entered_at": NOW,
    }
    fields.update(overrides)
    return Case(**fields)


class TestCase:
    def test_a_working_case_has_no_closing_time(self) -> None:
        assert a_case().closed_at is None
        assert a_case().is_terminal is False
        assert a_case().category is StateCategory.AUTO

    def test_a_terminal_case_must_have_one(self) -> None:
        """I8: a closed case that cannot say when it closed is a broken audit trail."""
        with pytest.raises(DomainValidationError, match="closed_at"):
            a_case(state=CaseState.RESOLVED_UPHELD)

    def test_a_working_case_cannot_have_one(self) -> None:
        with pytest.raises(DomainValidationError, match="closed_at"):
            a_case(closed_at=NOW)

    def test_human_review_must_say_why(self) -> None:
        """I4 and I6: "with a person" is never a state a case reaches without a reason."""
        with pytest.raises(DomainValidationError, match="review_reason"):
            a_case(state=CaseState.NEEDS_HUMAN_REVIEW)

        case = a_case(
            state=CaseState.NEEDS_HUMAN_REVIEW,
            review_reason=ReviewReason(code=ReviewCode.ELIGIBILITY_UNKNOWN),
        )
        assert case.category is StateCategory.HUMAN

    def test_only_human_review_carries_a_reason(self) -> None:
        with pytest.raises(DomainValidationError, match="review_reason"):
            a_case(review_reason=ReviewReason(code=ReviewCode.ELIGIBILITY_UNKNOWN))

    def test_updated_at_cannot_precede_opened_at(self) -> None:
        with pytest.raises(DomainValidationError, match="precedes"):
            a_case(updated_at=datetime(2026, 2, 1, tzinfo=UTC))


class TestReviewReason:
    def test_module_unavailable_renders_with_its_module(self) -> None:
        """I6: the reason names which module, or a human cannot act on it."""
        reason = ReviewReason(code=ReviewCode.MODULE_UNAVAILABLE, module="email")
        assert reason.render() == "module_unavailable:email"

    def test_module_unavailable_needs_a_module(self) -> None:
        with pytest.raises(DomainValidationError, match="module"):
            ReviewReason(code=ReviewCode.MODULE_UNAVAILABLE)

    def test_other_codes_cannot_carry_one(self) -> None:
        with pytest.raises(DomainValidationError, match="module"):
            ReviewReason(code=ReviewCode.STEP_LIMIT, module="email")

    def test_a_plain_code_renders_as_itself(self) -> None:
        assert ReviewReason(code=ReviewCode.STEP_LIMIT).render() == "step_limit"


class TestPolicyVerdictLattice:
    def a_verdict(self, **overrides: object) -> PolicyVerdict:
        fields: dict[str, object] = {
            "id": VerdictId("vrd_01HQ"),
            "case_id": CaseId("case_01HQ"),
            "stage": PolicyStage.PLANNING,
            "verdict": VerdictKind.ALLOW,
            "rules_version": "v1",
            "evaluated_at": NOW,
        }
        fields.update(overrides)
        return PolicyVerdict(**fields)

    def test_the_strictest_wins(self) -> None:
        results = (
            RuleResult(rule_id="R01", verdict=VerdictKind.ALLOW, reason="ok"),
            RuleResult(rule_id="R02", verdict=VerdictKind.REQUIRE_APPROVAL, reason="high_value"),
        )
        verdict = self.a_verdict(verdict=VerdictKind.REQUIRE_APPROVAL, results=results)
        assert verdict.verdict is VerdictKind.REQUIRE_APPROVAL

    def test_a_verdict_below_its_inputs_is_refused(self) -> None:
        """The lattice is the safety argument: combining can only ever raise."""
        results = (
            RuleResult(rule_id="R02", verdict=VerdictKind.REQUIRE_APPROVAL, reason="high_value"),
        )
        with pytest.raises(DomainValidationError, match="maximum"):
            self.a_verdict(verdict=VerdictKind.ALLOW, results=results)

    def test_a_signal_floor_counts_too(self) -> None:
        """I7: a caution signal raises the verdict exactly like a rule does."""
        signals = (
            CautionSignal(
                kind="pattern_context_unavailable",
                min_verdict=VerdictKind.REQUIRE_APPROVAL,
                reason="provider unreachable",
                source_ref="cross_case",
            ),
        )
        with pytest.raises(DomainValidationError, match="maximum"):
            self.a_verdict(verdict=VerdictKind.ALLOW, signals=signals)

    def test_escalation_must_say_why(self) -> None:
        results = (RuleResult(rule_id="R21", verdict=VerdictKind.ESCALATE, reason="manipulation"),)
        with pytest.raises(DomainValidationError, match="review reason"):
            self.a_verdict(verdict=VerdictKind.ESCALATE, results=results)


class TestActionNeedsApproval:
    def an_action(self, **overrides: object) -> Action:
        fields: dict[str, object] = {
            "id": ActionId("act_01HQ"),
            "case_id": CaseId("case_01HQ"),
            "intent_id": IntentId("int_01HQ"),
            "action_type": ActionType.SEND_MESSAGE,
            "action_seq": 1,
            "idempotency_key": IdempotencyKey("key-1"),
            "payload_hash": HASH,
            "status": ActionStatus.PLANNED,
            "planned_at": NOW,
        }
        fields.update(overrides)
        return Action(**fields)

    def test_a_credit_without_an_approval_cannot_be_built(self) -> None:
        """I3, at the type rather than at the executor: no credit without an approval."""
        with pytest.raises(DomainValidationError, match="I3"):
            self.an_action(action_type=ActionType.POST_CREDIT)

    def test_a_credit_with_one_is_fine(self) -> None:
        action = self.an_action(
            action_type=ActionType.POST_CREDIT,
            approval_id=ApprovalId("apr_01HQ"),
            credit_shipment_ref="SHP-0001",
            credit_reason_code="calculation_error",
        )
        assert action.approval_id == ApprovalId("apr_01HQ")

    def test_a_credit_without_its_index_keys_is_refused(self) -> None:
        """ux_actions_one_credit indexes (credit_shipment_ref, credit_reason_code). Left
        unset, the index would be indexing NULLs and refusing nothing (I3). Found by the
        Phase 1 safety review."""
        with pytest.raises(DomainValidationError, match="ux_actions_one_credit"):
            self.an_action(action_type=ActionType.POST_CREDIT, approval_id=ApprovalId("apr_01HQ"))

    def test_a_message_must_not_carry_them(self) -> None:
        with pytest.raises(DomainValidationError, match="exactly for POST_CREDIT"):
            self.an_action(credit_shipment_ref="SHP-0001", credit_reason_code="x")


class TestEvidenceNeverInvents:
    def a_result(self, **overrides: object) -> EvidenceResult:
        fields: dict[str, object] = {
            "requirement_key": "shipment.measured_dims",
            "source": "shipment_api",
            "status": EvidenceStatus.FOUND,
            "value": {"length_cm": 30},
            "retrieved_at": NOW,
        }
        fields.update(overrides)
        return EvidenceResult(**fields)

    def test_found_carries_a_value(self) -> None:
        assert self.a_result().value == {"length_cm": 30}

    def test_missing_cannot_carry_one(self) -> None:
        with pytest.raises(DomainValidationError, match="FOUND"):
            self.a_result(status=EvidenceStatus.MISSING)

    def test_missing_without_a_value_is_fine(self) -> None:
        assert self.a_result(status=EvidenceStatus.MISSING, value=None).value is None

    def test_failed_must_say_what_went_wrong(self) -> None:
        with pytest.raises(DomainValidationError, match="error_code"):
            self.a_result(status=EvidenceStatus.FAILED, value=None)

        failed = self.a_result(status=EvidenceStatus.FAILED, value=None, error_code="timeout")
        assert failed.error_code == "timeout"
