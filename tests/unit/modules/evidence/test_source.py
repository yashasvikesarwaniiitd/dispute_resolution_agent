"""Card P2-05: three answers and no fourth, one test per fault token.

The distinction this file exists to defend is MISSING against FAILED. "There is nothing
there" is a fact about the shipment; "we could not find out" is a fact about the fetch. A
source that collapses them hands the calculator an outage dressed up as evidence, and I4
turns into a formality.

`test_a_permanent_fault_is_not_transient` is the one D41 reads: a transient failure is
retried three times in a gather round and a permanent one is not retried at all. Getting it
the wrong way round means hammering a dead endpoint or giving up on a blip, and only the
adapter knows which it had (scenario S21).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.evidence import EvidenceRequirement
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import (
    PERMANENT_FAULTS,
    REQUIREMENT_KEYS,
    TRANSIENT_FAULTS,
    SyntheticShipmentSource,
    is_transient,
)

CASE = CaseId("case_p205")
FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact="seller+1@example.invalid",
    raised_at=datetime(2026, 1, 1, tzinfo=UTC),
)


def need(key: str, *, required: bool = True) -> EvidenceRequirement:
    return EvidenceRequirement(key=key, source="evidence", required=required)


def source(name: str = "kurta_box_larger", **faults: str) -> SyntheticShipmentSource:
    return SyntheticShipmentSource(fixture=fixture(name), faults=dict(faults))


def after_the_finding(name: str) -> SyntheticShipmentSource:
    """The same source once the hub has answered. A finding is a reply to a remeasurement
    we asked for, so it is absent until it arrives - see `finding_available`."""
    return SyntheticShipmentSource(fixture=fixture(name), finding_available=True)


class TestTheThreeAnswers:
    @pytest.mark.parametrize("key", REQUIREMENT_KEYS)
    def test_every_documented_key_answers_without_raising(self, key: str) -> None:
        """A key the pack asks for and the source has never heard of would surface as a
        crash in a gather round rather than as a MISSING item."""
        result = source().fetch(CASE, FACTS, need(key))
        assert result.status in {EvidenceStatus.FOUND, EvidenceStatus.MISSING}

    def test_a_present_requirement_is_found_with_a_value(self) -> None:
        result = source().fetch(CASE, FACTS, need("shipment.measured_dims"))
        assert result.status is EvidenceStatus.FOUND
        assert result.value is not None
        assert result.error_code is None

    def test_an_absent_requirement_is_missing_with_no_value(self) -> None:
        """`kurta_box_larger` carries no ops finding, which is nothing-there, not a failure."""
        result = source().fetch(CASE, FACTS, need("ops.finding"))
        assert result.status is EvidenceStatus.MISSING
        assert result.value is None
        assert result.error_code is None

    def test_an_unknown_key_is_missing_rather_than_invented(self) -> None:
        """The contract of an evidence source: it never makes a value up."""
        result = source().fetch(CASE, FACTS, need("shipment.colour"))
        assert result.status is EvidenceStatus.MISSING
        assert result.value is None


class TestTheFaultVocabulary:
    def test_a_timeout_is_failed_with_a_code_not_missing(self) -> None:
        """Returning MISSING here would read as "the shipment has no measurements"."""
        result = source(**{"shipment.measured_dims": "timeout"}).fetch(
            CASE, FACTS, need("shipment.measured_dims")
        )
        assert result.status is EvidenceStatus.FAILED
        assert result.error_code == "timeout"
        assert result.value is None

    def test_a_permanent_error_is_failed_with_its_own_code(self) -> None:
        result = source(**{"ledger.entries": "error_permanent"}).fetch(
            CASE, FACTS, need("ledger.entries")
        )
        assert result.status is EvidenceStatus.FAILED
        assert result.error_code == "error_permanent"

    def test_timeout_twice_fails_twice_then_works(self) -> None:
        """A source that only ever failed, or only ever worked, would let a broken retry
        loop pass its tests. This one needs the loop to actually count (S05c)."""
        src = source(**{"shipment.zone": "timeout_twice"})
        statuses = [src.fetch(CASE, FACTS, need("shipment.zone")).status for _ in range(3)]
        assert statuses == [EvidenceStatus.FAILED, EvidenceStatus.FAILED, EvidenceStatus.FOUND]

    def test_a_timeout_never_recovers_on_its_own(self) -> None:
        """The positive control for the test above: `timeout` is not `timeout_twice`."""
        src = source(**{"shipment.zone": "timeout"})
        statuses = {src.fetch(CASE, FACTS, need("shipment.zone")).status for _ in range(5)}
        assert statuses == {EvidenceStatus.FAILED}

    def test_a_fault_on_one_key_leaves_the_others_alone(self) -> None:
        src = source(**{"ledger.entries": "error_permanent"})
        assert src.fetch(CASE, FACTS, need("shipment.zone")).status is EvidenceStatus.FOUND


class TestDisabledIsNotAFailure:
    def test_an_unavailable_module_raises_rather_than_returning_failed(self) -> None:
        """I6: a switched-off module sends the case to a human with its name, and a FAILED
        item would instead be assessed as weak evidence."""
        src = source(**{"shipment.zone": "unavailable"})
        with pytest.raises(ModuleUnavailable, match="evidence"):
            src.fetch(CASE, FACTS, need("shipment.zone"))

    def test_nothing_else_escapes_as_an_exception(self) -> None:
        """Every other failure degrades the evidence instead of crashing the case."""
        for fault in TRANSIENT_FAULTS | PERMANENT_FAULTS:
            src = source(**{"shipment.zone": fault})
            assert src.fetch(CASE, FACTS, need("shipment.zone")).status is EvidenceStatus.FAILED


class TestWhatTheRetryLoopReads:
    def test_transient_faults_are_retryable(self) -> None:
        assert all(is_transient(code) for code in TRANSIENT_FAULTS)

    def test_a_permanent_fault_is_not_transient(self) -> None:
        """S21: no retry timer is armed for this one."""
        assert not any(is_transient(code) for code in PERMANENT_FAULTS)

    def test_no_code_is_both(self) -> None:
        assert not TRANSIENT_FAULTS & PERMANENT_FAULTS

    def test_an_unknown_code_is_not_retried(self) -> None:
        """Erring towards not hammering something nobody classified."""
        assert not is_transient("something_new")
        assert not is_transient(None)


class TestFreeTextTravelsLabelled:
    def test_an_ops_note_comes_back_as_untrusted_text(self) -> None:
        """I5: a note typed by a person at a hub is outside text wherever it is stored."""
        result = after_the_finding("injection_in_ops_note").fetch(CASE, FACTS, need("ops.finding"))
        assert result.status is EvidenceStatus.FOUND
        assert len(result.untrusted) == 1
        assert result.untrusted[0].origin == "jira_comment"

    def test_the_note_is_not_inlined_into_the_value(self) -> None:
        """The value holds the measurements; the sentence travels beside them, labelled."""
        result = after_the_finding("injection_in_ops_note").fetch(CASE, FACTS, need("ops.finding"))
        assert result.value is not None
        assert "note" not in result.value

    def test_a_structured_finding_with_no_note_carries_no_untrusted_text(self) -> None:
        source = after_the_finding("scanner_fault")
        assert source.fetch(CASE, FACTS, need("ops.finding")).untrusted == ()

    def test_a_finding_that_has_not_come_back_yet_is_missing(self) -> None:
        """S06: round 1 of `scanner_fault` has no finding, because nobody has remeasured
        anything yet. Serving it early would prove a credit before the remeasurement."""
        result = source("scanner_fault").fetch(CASE, FACTS, need("ops.finding"))
        assert result.status is EvidenceStatus.MISSING


class TestNumbersSurviveTheTrip:
    def test_dimensions_come_back_as_strings_a_decimal_can_be_rebuilt_from(self) -> None:
        """I9 and ADR 0011: a dimension that goes through JSON as a float arrives as
        30.000000000000004, and the slab it lands in is a seller's money."""
        result = source().fetch(CASE, FACTS, need("shipment.measured_dims"))
        assert result.value is not None
        assert Decimal(str(result.value["length_cm"])) == Decimal("30")

    def test_weights_and_money_come_back_as_integers(self) -> None:
        dims = source().fetch(CASE, FACTS, need("shipment.measured_dims"))
        ledger = source().fetch(CASE, FACTS, need("ledger.entries"))
        assert dims.value is not None
        assert ledger.value is not None
        assert isinstance(dims.value["dead_grams"], int)
        entries = ledger.value["entries"]
        assert isinstance(entries, list)
        assert all(isinstance(e["amount_paise"], int) for e in entries)


class TestDeterminism:
    def test_the_same_fixture_gives_the_same_evidence(self) -> None:
        """A scenario that cannot be re-run is not a scenario (CLAUDE.md section 10)."""
        first = source().fetch(CASE, FACTS, need("shipment.measured_dims")).value
        second = source().fetch(CASE, FACTS, need("shipment.measured_dims")).value
        assert first == second

    def test_a_source_with_no_fixture_finds_nothing_rather_than_inventing_one(self) -> None:
        empty = SyntheticShipmentSource()
        assert empty.fetch(CASE, FACTS, need("shipment.zone")).status is EvidenceStatus.MISSING
