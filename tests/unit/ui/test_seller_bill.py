"""The seller's bill says what the rate card gives, and the table shows how each ticket ended."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

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
