"""The seller catalogue (ADR 0060): the approved dataset, read and checked as the agent sees it.

Run in the frozen environment (ADR 0072): the three approved sellers alone, so every assertion
here is the approved data's and the generated sellers can never move it.
"""

from __future__ import annotations

import pytest

from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FINDINGS, fixture
from dispute_agent.packs.weight_dispute.calculator import calculate
from dispute_agent.packs.weight_dispute.rate_card import DEFAULT_RATE_CARD

pytestmark = pytest.mark.usefixtures("approved_catalogue")

#: The two disputes that deliberately carry a billing error.
BILLING_ERRORS = {"tl_dress_wrong_slab", "nc_lamp_duplicate"}


def test_three_sellers_seventy_three_past_shipments_and_eleven_disputes() -> None:
    files = catalogue().sellers
    assert [f.seller.business for f in files] == [
        "CaseKraft Accessories",
        "NorthCart Essentials",
        "ThreadLane Apparel",
    ]
    rows = [r for f in files for r in f.shipments]
    assert sum(r.role == "history" for r in rows) == 73
    assert sum(r.role == "dispute" for r in rows) == 11
    assert all(f.seller.synthetic for f in files)


def test_every_bill_follows_the_rate_card_except_the_deliberate_errors() -> None:
    for built in catalogue().by_ref.values():
        s = built.shipment
        card = calculate(s.measured, s.measured_dead_grams, s.zone, DEFAULT_RATE_CARD)
        if built.name == "tl_dress_wrong_slab":
            assert s.billed_paise > card.amount_paise
        else:
            assert s.billed_paise == card.amount_paise, built.name
    duplicate = fixture("nc_lamp_duplicate").shipment.ledger
    assert len(duplicate) == 2 and duplicate[0].amount_paise == duplicate[1].amount_paise


def test_history_predates_every_dispute_of_its_seller() -> None:
    for seller in catalogue().sellers:
        first_dispute = min(r.measured_at for r in seller.shipments if r.role == "dispute")
        assert all(r.measured_at < first_dispute for r in seller.shipments if r.role == "history")


def test_a_comparison_only_ever_uses_its_own_sellers_shipments() -> None:
    owner = {ref: b.shipment.seller_id for ref, b in catalogue().by_ref.items()}
    for built in catalogue().by_ref.values():
        refs = (built.comparable_history or {}).get("reference_refs", [])
        assert {owner[r] for r in refs} <= {built.shipment.seller_id}


def test_the_six_scenarios_compare_as_designed() -> None:
    verdict = {
        name: fixture(name).comparable_history or {}
        for name in (
            "ck_tray_anomaly",
            "ck_tray_confirmed",
            "tl_tee_carton",
            "nc_lamp_duplicate",
            "nc_kettle_new",
            "nc_lamp_underdeclared",
        )
    }
    assert verdict["ck_tray_anomaly"]["verdict"] == "anomalous"
    assert verdict["ck_tray_confirmed"]["excluded"] == {"disputed": 1}  # A is under dispute
    assert verdict["tl_tee_carton"]["verdict"] == "not_comparable"
    assert verdict["tl_tee_carton"]["differs_by"] == ["packaging", "quantity"]
    assert verdict["tl_tee_carton"]["flags"]["packaging_consistent"] is True
    assert verdict["nc_lamp_duplicate"]["verdict"] == "normal"
    assert verdict["nc_kettle_new"]["verdict"] == "insufficient_history"
    assert verdict["nc_lamp_underdeclared"]["flags"]["declared_below_history"] is True


def test_the_hubs_correction_is_not_in_the_data_only_in_the_findings_bank() -> None:
    confirmed = fixture("ck_tray_confirmed")
    assert confirmed.finding is None  # nothing for the hub to have "already said" at intake
    history = confirmed.comparable_history or {}
    assert history["current"]["sides_cm"] == ["34", "22", "15"]
    assert FINDINGS["tray_mode_corrected"].kind == "remeasured"


def test_scenario_b_the_carton_measured_a_little_taller_crosses_one_slab() -> None:
    # The seller declared 5 slabs (INR 190); the hub's carton, under 5% bigger, is 6 (INR 228)
    # and the bill follows the hub. A real reason to dispute, inside the rules' tolerance.
    s = fixture("tl_tee_carton").shipment
    declared = calculate(s.declared, s.declared_dead_grams, s.zone, DEFAULT_RATE_CARD)
    measured = calculate(s.measured, s.measured_dead_grams, s.zone, DEFAULT_RATE_CARD)
    assert (declared.slabs, measured.slabs) == (5, 6)
    assert s.billed_paise == measured.amount_paise == 22800 > declared.amount_paise


def test_every_dispute_gives_the_seller_a_reason_to_dispute() -> None:
    # A seller disputes a bill above what they declared, or a charge taken twice; a dispute
    # whose bill equals the declaration is not one a real seller raises.
    for seller in catalogue().sellers:
        for row in seller.shipments:
            if row.role != "dispute":
                continue
            assert row.fixture is not None, row.ref  # every dispute names its fixture
            s = fixture(row.fixture).shipment
            declared = calculate(s.declared, s.declared_dead_grams, s.zone, DEFAULT_RATE_CARD)
            assert s.billed_paise > declared.amount_paise or row.duplicate_debit, row.ref
