"""The seller's bill says what the rate card gives, and the table shows how each ticket ended."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from dispute_agent.ui.seller import catalogue, latest_ticket_for, open_ticket_for

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _by_ref() -> dict[str, object]:
    return {s.ref: s for s in catalogue()}


def test_a_wrong_bill_says_what_the_rate_card_gives_and_by_how_much_it_is_over() -> None:
    """SHP-000002: same size declared and measured, so only the card shows the error."""
    s = _by_ref()["SHP-000002"]
    assert s.charged == "₹2,800.00"  # type: ignore[attr-defined]
    assert s.card_on_measured == "₹980.00 for 7.00 kg"  # type: ignore[attr-defined]
    assert s.card_on_declared == "₹980.00 for 7.00 kg"  # type: ignore[attr-defined]
    assert s.over_card == "₹1,820.00"  # type: ignore[attr-defined]


def test_a_right_bill_on_a_bigger_measurement_shows_what_the_declared_size_would_cost() -> None:
    """SHP-000001: the bill matches the hub's size; the dispute is about the size itself."""
    s = _by_ref()["SHP-000001"]
    assert s.card_on_measured.startswith("₹152.00")  # type: ignore[attr-defined]
    assert s.card_on_declared.startswith("₹76.00")  # type: ignore[attr-defined]
    assert s.over_card == ""  # type: ignore[attr-defined]


def test_the_table_shows_the_card_on_the_declared_weight_beside_the_charge() -> None:
    """The two table columns: what the seller expects to pay, and what they were billed."""
    rows = _by_ref()
    assert rows["SHP-000002"].on_declared == "₹980.00"  # type: ignore[attr-defined]
    assert rows["SHP-000001"].on_declared == "₹76.00"  # type: ignore[attr-defined]
    assert all(s.on_declared for s in catalogue())  # every row has a figure


def _ticket(ref: str, *, is_open: bool, at: datetime, label: str) -> Any:  # Any: a stand-in
    return SimpleNamespace(shipment_ref=ref, is_open=is_open, updated_at=at, label=label)


def _shipment(ref: str) -> Any:  # Any: a stand-in for a Shipment
    return SimpleNamespace(ref=ref)


def test_a_resolved_ticket_still_shows_beside_its_shipment() -> None:
    credited = _ticket("SHP-000002", is_open=False, at=T0, label="Resolved: credited")
    mine = [credited, _ticket("SHP-000001", is_open=True, at=T0, label="With a person")]
    assert latest_ticket_for(_shipment("SHP-000002"), mine) is credited
    assert open_ticket_for(_shipment("SHP-000002"), mine) is None  # it may be raised again


def test_an_open_ticket_wins_over_an_older_closed_one() -> None:
    old = _ticket("SHP-000001", is_open=False, at=T0, label="Closed")
    new = _ticket("SHP-000001", is_open=True, at=T0 - timedelta(days=1), label="With a person")
    assert latest_ticket_for(_shipment("SHP-000001"), [old, new]) is new
    assert latest_ticket_for(_shipment("SHP-000099"), [old]) is None


def test_the_bill_shows_every_step_of_the_rate_card() -> None:
    # SHP-000005: the scanner read it bigger, so the size, not the scale, sets the charge.
    from dispute_agent.ui.seller import DIVIDE, TIMES

    s = {x.ref: x for x in catalogue()}["SHP-000005"]
    plain = [tuple(c.replace(TIMES, "x").replace(DIVIDE, "/") for c in row) for row in s.breakdown]
    assert plain == [
        ("Size (L x W x H)", "20 x 15 x 10 cm", "34 x 22 x 15 cm"),
        ("Scale weight", "0.48 kg", "1.10 kg"),
        ("Size weight (L x W x H / 5,000)", "0.60 kg", "2.244 kg"),
        ("Charged weight (the higher)", "0.60 kg", "2.244 kg"),
        ("Slabs of 0.50 kg (rounded up)", "2", "5"),
        ("Rate per slab (zone B)", "₹38.00", "₹38.00"),
        ("Charge (slabs x rate)", "₹76.00", "₹190.00"),
    ]
    assert s.verdict.startswith("Your bill matches the hub's measurement.")


def test_the_verdict_says_what_there_is_to_dispute() -> None:
    from dispute_agent.ui.seller import verdict

    assert verdict(280000, 98000, 98000).startswith("You were charged ₹1,820.00 more")
    assert verdict(19000, 7600, 19000).startswith("Your bill matches the hub's measurement.")
    assert verdict(7600, 7600, 7600).endswith("There is nothing to dispute.")
    # A second debit of a bill that matches the card is still an error (SHP-101025).
    assert verdict(15200, 15200, 15200, debits=2).startswith(
        "You were charged ₹152.00 twice for this one shipment."
    )


def test_a_shipment_debited_twice_reads_as_a_billing_error() -> None:
    from dispute_agent.ui.seller import seller_shipments

    lamp = next(s for s in seller_shipments("SELL-0101") if s.ref == "SHP-101025")
    assert lamp.billing_error and "extra charge is a billing error" in lamp.verdict
    assert "nothing to dispute" not in lamp.verdict


@pytest.mark.usefixtures("approved_catalogue")
def test_each_demo_seller_sees_only_their_own_shipments() -> None:
    # ADR 0060: the seller page lists the selected demo seller's shipments and no one else's.
    from dispute_agent.ui.seller import personas, seller_shipments

    people = personas()
    assert [p["business"] for p in people] == [
        "CaseKraft Accessories",
        "NorthCart Essentials",
        "ThreadLane Apparel",
    ]
    seen: set[str] = set()
    disputes = {"CaseKraft Accessories": 3, "NorthCart Essentials": 4, "ThreadLane Apparel": 4}
    for person in people:
        rows = seller_shipments(person["seller_id"])
        assert len(rows) == 28 and sum(r.disputed for r in rows) == disputes[person["business"]]
        assert {r.seller_ref for r in rows} == {person["seller_id"]}
        refs = {r.ref for r in rows}
        assert not refs & seen
        seen |= refs


def test_a_sellers_address_is_their_own_tagged_one_or_none() -> None:
    from dispute_agent.ui.seller import contact_for

    allow = "owner+casekraft@gmail.com, owner+threadlane@gmail.com"
    assert contact_for("casekraft", allow) == "owner+casekraft@gmail.com"
    assert contact_for("northcart", allow) is None  # never another seller's address
    assert contact_for("northcart", None) is None
