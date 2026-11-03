"""Card P2-01: may this dispute proceed at all?

Three outcomes, and the third is the one with teeth. `test_an_undeterminable_status_does_not
_guess` is scenario S46: when nothing says what happened to the parcel, the case goes to a
person. Guessing "delivered" credits a parcel that never moved; guessing "not delivered"
refuses a seller on a status nobody could confirm, and from their side that is
indistinguishable from a refusal on the merits.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.packs.weight_dispute.eligibility import (
    NOT_DELIVERED_CODE,
    STATUS_KEY,
    STATUS_UNKNOWN_CODE,
    eligibility,
)
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

PACK = WeightDisputePack()


def facts(**pack_facts: str) -> CaseFacts:
    return CaseFacts(
        shipment_ref="SHP-000001",
        claim_ref="CLM-000001",
        seller_ref="SELL-0001",
        seller_contact="seller+1@example.invalid",
        raised_at=datetime(2026, 2, 1, tzinfo=UTC),
        pack_facts=dict(pack_facts),
    )


def from_fixture(name: str) -> CaseFacts:
    """The facts intake would build from a fixture's shipment."""
    built = fixture(name)
    return facts(**{STATUS_KEY: built.shipment.status})


class TestDelivered:
    def test_a_delivered_shipment_is_eligible(self) -> None:
        assert eligibility(from_fixture("kurta_box_larger")).outcome == "eligible"

    def test_an_eligible_result_carries_no_reason_codes(self) -> None:
        """Nothing to explain. A reason on an eligible case would end up quoted somewhere."""
        assert eligibility(from_fixture("kurta_box_larger")).reason_codes == ()


class TestNotDelivered:
    def test_an_rto_shipment_is_ineligible(self) -> None:
        """A parcel that came back was never carried, so there is nothing to dispute."""
        result = eligibility(from_fixture("not_delivered"))
        assert result.outcome == "ineligible"
        assert result.reason_codes == (NOT_DELIVERED_CODE,)

    @pytest.mark.parametrize("status", ["RTO", "RETURNED", "CANCELLED", "LOST"])
    def test_every_documented_non_delivery_status_is_ineligible(self, status: str) -> None:
        assert eligibility(facts(**{STATUS_KEY: status})).outcome == "ineligible"


class TestUnknown:
    def test_an_undeterminable_status_does_not_guess(self) -> None:
        """S46. The only scenario for the eligibility step's escalate branch."""
        result = eligibility(facts())
        assert result.outcome == "unknown"
        assert result.reason_codes == (STATUS_UNKNOWN_CODE,)

    def test_an_unrecognised_status_is_unknown_not_ineligible(self) -> None:
        """A status this pack has not been taught is not evidence that nothing was carried.
        Treating it as a refusal would turn every new carrier status into a rejected seller."""
        assert eligibility(facts(**{STATUS_KEY: "IN_TRANSIT"})).outcome == "unknown"

    def test_an_empty_status_is_unknown(self) -> None:
        assert eligibility(facts(**{STATUS_KEY: ""})).outcome == "unknown"


class TestThroughThePack:
    def test_the_pack_exposes_the_same_answer(self) -> None:
        assert PACK.eligibility(from_fixture("not_delivered")).outcome == "ineligible"

    def test_the_evidence_plan_is_not_empty_and_names_one_source(self) -> None:
        plan = PACK.evidence_plan(from_fixture("kurta_box_larger"))
        assert plan
        assert {r.source for r in plan} == {"evidence"}

    def test_the_plan_is_the_same_for_every_case(self) -> None:
        """It is a property of the dispute kind, not of the parcel. A plan that varied per
        case would make a missing item impossible to tell from one never asked for."""
        assert PACK.evidence_plan(from_fixture("kurta_box_larger")) == PACK.evidence_plan(
            from_fixture("not_delivered")
        )


class TestParseFacts:
    def test_flat_scalars_pass(self) -> None:
        assert PACK.parse_facts({"sku": "SKU-0001", "zone": "B", "slabs": 4}) == {
            "sku": "SKU-0001",
            "zone": "B",
            "slabs": 4,
        }

    def test_a_float_is_refused(self) -> None:
        """ADR 0011: weight and money are integers, dimensions are Decimal. A float in
        pack_facts is a number that will be wrong by the time it reaches a seller."""
        with pytest.raises(Exception, match="float"):
            PACK.parse_facts({"declared_kg": cast("Any", 0.48)})

    def test_a_nested_value_is_refused_rather_than_flattened(self) -> None:
        """The kernel stores these as scalars. Quietly stringifying a dict would put a
        value in the database that nothing can read back."""
        with pytest.raises(Exception, match="flat scalar"):
            PACK.parse_facts({"dims": {"length_cm": "20"}})
