"""Card P2-07: the three provable credit reasons, and the things that are not proof.

One test per reason using its named fixture, plus the two that matter more:

`test_multi_error` — two proven errors means no credit, a person decides (R14, S38). Two
proofs of overcharge are two different numbers, and netting them automatically is how a
seller gets paid twice for one mistake.

`test_free_text_never_proves` — a seller writing "I was charged twice" against a clean ledger
proves nothing. That is D29 and I7, and it is tested here as well as asserted structurally,
because the structural argument (there is no parameter a sentence could arrive through) is
only as good as it stays.
"""

from __future__ import annotations

import inspect

import pytest

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.packs.weight_dispute.calculator import Dimensions
from dispute_agent.packs.weight_dispute.rate_card import DEFAULT_RATE_CARD as CARD
from dispute_agent.packs.weight_dispute.reconcile import (
    CALCULATION_ERROR,
    DUPLICATE_CHARGE,
    REASON_CODES,
    REMEASUREMENT_LOWER,
    LedgerLine,
    credit_amount,
    prove,
)


def lines(built: object) -> list[LedgerLine]:
    return [
        LedgerLine(
            transaction_id=entry.transaction_id,
            kind=entry.kind,
            amount_paise=entry.amount_paise,
            claim_ref=entry.claim_ref,
            reason_code=entry.reason_code,
        )
        for entry in built.shipment.ledger  # type: ignore[attr-defined]
    ]


def reconcile_fixture(name: str, **overrides: object):  # noqa: ANN201
    built = fixture(name)
    shipment = built.shipment
    kwargs: dict[str, object] = {
        "charged": Money.from_paise(shipment.billed_paise),
        "billed_dimensions": shipment.measured,
        "billed_dead_grams": shipment.measured_dead_grams,
        "zone": shipment.zone,
        "card": CARD,
        "ledger": lines(built),
        "claim_ref": shipment.claim_ref,
        "packaging": shipment.packaging,
    }
    kwargs.update(overrides)
    return prove(**kwargs)  # type: ignore[arg-type]


class TestNoErrorWhenTheBillIsRight:
    def test_the_kurta_case_proves_nothing(self) -> None:
        """Billed correctly on the measured values. The seller is unhappy and still right
        to have asked, but there is no overcharge to credit."""
        result = reconcile_fixture("kurta_box_larger")
        assert result.definite_errors == ()
        assert result.recalculated == Money.of("152.00")


class TestCalculationError:
    def test_a_wrong_slab_is_proven(self) -> None:
        result = reconcile_fixture("calc_error_wrong_slab")
        assert result.definite_errors == (CALCULATION_ERROR,)

    def test_the_credit_is_exactly_the_overcharge(self) -> None:
        result = reconcile_fixture("calc_error_wrong_slab")
        assert credit_amount(result, CALCULATION_ERROR) == Money.of("38.00")

    def test_a_keyed_weight_is_proven_too(self) -> None:
        result = reconcile_fixture("calc_error_high_value")
        assert result.definite_errors == (CALCULATION_ERROR,)
        assert credit_amount(result, CALCULATION_ERROR) == Money.of("1820.00")

    def test_an_undercharge_proves_nothing(self) -> None:
        """Recalculating higher than the bill is not this system's business."""
        result = reconcile_fixture(
            "kurta_box_larger", charged=Money.of("100.00")
        )
        assert CALCULATION_ERROR not in result.definite_errors


class TestRemeasurementLower:
    def test_a_structured_finding_proves_it(self) -> None:
        """scanner_fault: SCN-03-2 read +40 mm, the hub remeasured 22 x 15 x 10."""
        built = fixture("scanner_fault")
        assert built.finding is not None
        result = reconcile_fixture(
            "scanner_fault",
            remeasured=built.finding.measured,
            remeasured_dead_grams=built.finding.measured_dead_grams,
        )
        assert REMEASUREMENT_LOWER in result.definite_errors

    def test_the_credit_uses_the_remeasured_charge(self) -> None:
        built = fixture("scanner_fault")
        assert built.finding is not None
        result = reconcile_fixture(
            "scanner_fault",
            remeasured=built.finding.measured,
            remeasured_dead_grams=built.finding.measured_dead_grams,
        )
        owed = credit_amount(result, REMEASUREMENT_LOWER)
        assert owed == result.charged - (result.recalculated_remeasured or Money.of("0.00"))

    def test_without_a_finding_nothing_is_proven(self) -> None:
        """A seller's photo of a smaller box is not a remeasurement (money-rules section 2)."""
        result = reconcile_fixture("scanner_fault")
        assert REMEASUREMENT_LOWER not in result.definite_errors

    def test_a_finding_with_no_measurements_proves_nothing(self) -> None:
        """`inconclusive_still_checking` carries no structured fields on purpose."""
        result = reconcile_fixture("scanner_fault", remeasured=None, remeasured_dead_grams=None)
        assert REMEASUREMENT_LOWER not in result.definite_errors


class TestDuplicateCharge:
    def test_two_debits_on_one_line_are_proven(self) -> None:
        result = reconcile_fixture("duplicate_debit")
        assert DUPLICATE_CHARGE in result.definite_errors

    def test_the_credit_is_the_extra_debit_only(self) -> None:
        """The first DEBIT is the charge; the second is the duplicate."""
        result = reconcile_fixture("duplicate_debit")
        assert credit_amount(result, DUPLICATE_CHARGE) == Money.of("152.00")

    def test_one_debit_proves_nothing(self) -> None:
        result = reconcile_fixture("kurta_box_larger")
        assert DUPLICATE_CHARGE not in result.definite_errors

    def test_a_debit_on_another_charge_line_is_not_a_duplicate(self) -> None:
        """Same shipment, different claim: two charges, not one charged twice."""
        built = fixture("kurta_box_larger")
        other = LedgerLine(
            transaction_id="TXN-OTHER",
            kind="DEBIT",
            amount_paise=15200,
            claim_ref="CLM-999999",
        )
        result = reconcile_fixture("kurta_box_larger", ledger=[*lines(built), other])
        assert DUPLICATE_CHARGE not in result.definite_errors


class TestTwoErrorsMeanNoCredit:
    """R14 and scenario S38."""

    def test_multi_error_proves_two(self) -> None:
        result = reconcile_fixture("multi_error")
        assert len(result.definite_errors) == 2
        assert set(result.definite_errors) == {CALCULATION_ERROR, DUPLICATE_CHARGE}

    def test_there_is_no_single_reason_to_name(self) -> None:
        """A proposal has to name one reason. With two proven, there is none to name, and
        the case goes to a person instead."""
        assert reconcile_fixture("multi_error").single_error is None

    def test_one_error_does_give_a_single_reason(self) -> None:
        assert reconcile_fixture("calc_error_wrong_slab").single_error == CALCULATION_ERROR


class TestFreeTextNeverProves:
    def test_a_seller_claiming_a_duplicate_against_a_clean_ledger_proves_nothing(self) -> None:
        """D29, I7. The claim is a reason to look at the ledger, not evidence in itself."""
        built = fixture("duplicate_debit")
        assert built.statement is not None
        assert "charged twice" in built.statement

        clean = [line for line in lines(built) if line.transaction_id.endswith("-1")]
        result = reconcile_fixture("duplicate_debit", ledger=clean)
        assert result.definite_errors == ()

    def test_prove_has_no_parameter_a_sentence_could_arrive_through(self) -> None:
        """Structural, not behavioural. A free-text argument would be the hole; there is
        none, and this fails the moment somebody adds one."""
        parameters = set(inspect.signature(prove).parameters) - {"self"}
        for suspicious in ("statement", "note", "reply", "text", "reading", "summary"):
            assert suspicious not in parameters

    def test_an_injected_note_cannot_reach_the_reconciliation(self) -> None:
        """injection_in_ops_note carries corpus item 1 on the finding. Only the structured
        kind and measurements are passed, so the note has nowhere to go."""
        built = fixture("injection_in_ops_note")
        assert built.finding is not None
        assert built.finding.note is not None
        result = reconcile_fixture("injection_in_ops_note")
        assert REMEASUREMENT_LOWER not in result.definite_errors


class TestTheReasonVocabularyIsClosed:
    def test_only_three_reasons_exist(self) -> None:
        """Adding one needs a pack rule that proves it and an ADR (money-rules section 2)."""
        assert REASON_CODES == {CALCULATION_ERROR, REMEASUREMENT_LOWER, DUPLICATE_CHARGE}

    def test_it_matches_the_shipped_policy(self) -> None:
        from dispute_agent.kernel.policy.config import PolicyConfig

        assert PolicyConfig().credits.reason_codes == REASON_CODES

    def test_an_unknown_reason_is_refused(self) -> None:
        result = reconcile_fixture("calc_error_wrong_slab")
        with pytest.raises(DomainValidationError, match="not a proven"):
            credit_amount(result, "because_the_seller_asked")

    def test_an_unproven_reason_is_refused(self) -> None:
        """The reason has to be one *this* case proved, not merely a real reason code."""
        result = reconcile_fixture("calc_error_wrong_slab")
        with pytest.raises(DomainValidationError, match="not a proven"):
            credit_amount(result, DUPLICATE_CHARGE)


class TestNettingAgainstEarlierCredits:
    def test_a_second_dispute_cannot_pay_twice(self) -> None:
        """money-rules section 3: case A credited 200, case B proves the true charge was
        600, so case B owes 200 and the total credited is the real 400."""
        # Dead weight 10,000 g governs (volumetric is 7,000 g), 20 slabs, zone A at INR
        # 30.00 a slab: the true charge is INR 600.00 against a bill of INR 1,000.00.
        result = prove(
            charged=Money.of("1000.00"),
            billed_dimensions=Dimensions.of("40", "35", "25"),
            billed_dead_grams=10000,
            zone="A",
            card=CARD,
            ledger=[],
            claim_ref="CLM-000001",
        )
        assert CALCULATION_ERROR in result.definite_errors
        assert result.recalculated == Money.of("600.00")

        full = credit_amount(result, CALCULATION_ERROR)
        assert full == Money.of("400.00")
        netted = credit_amount(result, CALCULATION_ERROR, already_credited=Money.of("200.00"))
        assert netted == Money.of("200.00")

    def test_netting_never_goes_negative(self) -> None:
        result = reconcile_fixture("calc_error_wrong_slab")
        assert credit_amount(
            result, CALCULATION_ERROR, already_credited=Money.of("9999.00")
        ) == Money.of("0.00")
