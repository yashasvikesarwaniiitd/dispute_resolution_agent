"""The seller catalogue (ADR 0060): the approved dataset, read and checked as the agent sees it."""

from __future__ import annotations

from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FINDINGS, fixture
from dispute_agent.packs.weight_dispute.calculator import calculate
from dispute_agent.packs.weight_dispute.rate_card import DEFAULT_RATE_CARD

#: The two disputes that deliberately carry a billing error.
BILLING_ERRORS = {"tl_dress_wrong_slab", "nc_lamp_duplicate"}


def test_three_sellers_seventy_two_past_shipments_and_twelve_disputes() -> None:
    files = catalogue().sellers
    assert [f.seller.business for f in files] == [
        "CaseKraft Accessories",
        "NorthCart Essentials",
        "ThreadLane Apparel",
    ]
    rows = [r for f in files for r in f.shipments]
    assert sum(r.role == "history" for r in rows) == 72
    assert sum(r.role == "dispute" for r in rows) == 12
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


def test_scenario_b_charges_agree_in_one_slab() -> None:
    # Declared, measured and billed all land on 6 slabs (INR 228), so the bill is commercially
    # consistent with what the seller expected from the carton they declared.
    s = fixture("tl_tee_carton").shipment
    declared = calculate(s.declared, s.declared_dead_grams, s.zone, DEFAULT_RATE_CARD)
    measured = calculate(s.measured, s.measured_dead_grams, s.zone, DEFAULT_RATE_CARD)
    assert declared.slabs == measured.slabs == 6
    assert s.billed_paise == declared.amount_paise == 22800
