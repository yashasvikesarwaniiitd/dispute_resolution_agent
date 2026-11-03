"""Contract: EvidenceSource (kernel-interfaces.md 6.4).

Three answers, kept distinct: FOUND with a value, MISSING, FAILED with a code. The suite
exists mostly to stop a source collapsing them — returning MISSING when it actually timed
out reads as "we looked and there was nothing there", and the calculator would treat an
outage as a fact about the shipment.

Filled in by card P2-05. The bodies are deliberately adapter-agnostic: they ask for a
requirement every source must serve and a fault every source must be able to simulate, so a
real adapter in a later phase inherits the suite by adding one line to `ADAPTERS`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.evidence import EvidenceRequirement
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from tests.contract.conftest import build_or_skip, params, ready

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import EvidenceSource

CASE = CaseId("case_contract_evidence")
FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact="seller+1@example.invalid",
    raised_at=datetime(2026, 1, 1, tzinfo=UTC),
)

#: A requirement every source of shipment evidence serves, and one it can never have.
PRESENT = EvidenceRequirement(key="shipment.measured_dims", source="evidence", required=True)
ABSENT = EvidenceRequirement(key="shipment.nothing_serves_this", source="evidence", required=False)

ADAPTERS = [
    ready(
        "synthetic_shipments",
        lambda: SyntheticShipmentSource(
            fixture=fixture("injection_in_ops_note"), finding_available=True
        ),
    ),
]


@pytest.fixture(params=params(ADAPTERS))
def source(request: pytest.FixtureRequest) -> EvidenceSource:
    return cast("EvidenceSource", build_or_skip(request.param))


def with_fault(source: EvidenceSource, key: str, fault: str) -> EvidenceSource:
    """The same adapter, set up to fail on one key.

    Every adapter needs a way to be made to fail, or the FAILED half of the contract can
    never be tested against it. For the synthetic source that is the fault vocabulary; a
    real one will stub its transport.
    """
    if isinstance(source, SyntheticShipmentSource):
        return SyntheticShipmentSource(
            fixture=source.fixture,
            faults={key: fault},
            finding_available=source.finding_available,
        )
    pytest.skip(f"{type(source).__name__} has no way to inject a {fault}")


class TestFetching:
    def test_it_names_itself(self, source: EvidenceSource) -> None:
        assert isinstance(source.name, str)

    def test_a_known_requirement_comes_back_found_with_a_value(
        self, source: EvidenceSource
    ) -> None:
        result = source.fetch(CASE, FACTS, PRESENT)
        assert result.status is EvidenceStatus.FOUND
        assert result.value is not None
        assert result.requirement_key == PRESENT.key
        assert result.retrieved_at.tzinfo is not None

    def test_an_absent_requirement_comes_back_missing_with_no_value(
        self, source: EvidenceSource
    ) -> None:
        result = source.fetch(CASE, FACTS, ABSENT)
        assert result.status is EvidenceStatus.MISSING
        assert result.value is None
        assert result.error_code is None

    def test_a_timeout_comes_back_failed_with_a_code_not_missing(
        self, source: EvidenceSource
    ) -> None:
        """ "We could not find out" is not "there is nothing there"."""
        result = with_fault(source, PRESENT.key, "timeout").fetch(CASE, FACTS, PRESENT)
        assert result.status is EvidenceStatus.FAILED
        assert result.error_code
        assert result.value is None

    def test_only_module_unavailable_escapes_as_an_exception(self, source: EvidenceSource) -> None:
        """Every other failure is a FAILED result, so a flaky source degrades the evidence
        rather than crashing the case."""
        with pytest.raises(ModuleUnavailable):
            with_fault(source, PRESENT.key, "unavailable").fetch(CASE, FACTS, PRESENT)

        failing = with_fault(source, PRESENT.key, "error_permanent")
        assert failing.fetch(CASE, FACTS, PRESENT).status is EvidenceStatus.FAILED


class TestTrust:
    def test_free_text_inside_a_value_comes_back_as_untrusted_text(
        self, source: EvidenceSource
    ) -> None:
        """I5: a shipment note is written by a person, wherever it is stored."""
        note = EvidenceRequirement(key="ops.finding", source="evidence", required=False)
        result = source.fetch(CASE, FACTS, note)
        assert result.untrusted, "the fixture carries a note; it must arrive labelled"
        carried = result.untrusted[0]
        assert carried.text
        assert carried.content_hash
        # The sentence travels beside the measurements, never inlined into them.
        assert result.value is not None
        assert carried.text not in str(result.value)

    def test_numbers_come_back_as_decimals(self, source: EvidenceSource) -> None:
        """I9: these feed the calculator."""
        result = source.fetch(CASE, FACTS, PRESENT)
        assert result.value is not None
        for axis in ("length_cm", "width_cm", "height_cm"):
            # Carried as a string so JSON cannot turn it into a float on the way (ADR 0011).
            assert Decimal(str(result.value[axis])) > 0
        assert isinstance(result.value["dead_grams"], int)


class TestDeterminism:
    def test_the_same_seed_gives_the_same_evidence(self, source: EvidenceSource) -> None:
        """I11 and CLAUDE.md section 10: synthetic data is reproducible, or a failing
        scenario cannot be re-run."""
        assert source.fetch(CASE, FACTS, PRESENT).value == source.fetch(CASE, FACTS, PRESENT).value
