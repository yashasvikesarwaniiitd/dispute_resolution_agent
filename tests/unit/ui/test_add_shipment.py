"""The Add a shipment form's preview and save (ADR 0056), without Streamlit."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.modules.evidence.synthetic_shipments.added import (
    AddedShipments,
    ShipmentRejectedError,
)
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from dispute_agent.ui.add_shipment import preview, save
from tests.unit.modules.test_added_shipments import JPG, kurta

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("changes", "charged", "finding", "error"),
    [
        ({}, "₹190.00", "One error: ₹38.00 more than the rate card gives.", True),
        (
            {"charged_paise": None},
            "₹152.00",
            "No error: the bill matches the rate card for the hub's measurement.",
            False,
        ),
        (
            {"charged_paise": None, "charged_twice": True},
            "₹152.00 twice",
            "One error: charged twice. The second ₹152.00 is extra.",
            True,
        ),
        ({"charged_twice": True}, "₹190.00 twice", "Two errors", True),
        ({"charged_paise": 7600}, "₹76.00", "Charged less than the rate card gives", False),
        ({"status": "RTO"}, "₹190.00", "Not delivered", False),
    ],
)
def test_the_preview_says_what_code_finds(
    changes: dict[str, Any], charged: str, finding: str, error: bool
) -> None:
    seen = preview(kurta(**changes))
    assert (seen.expected, seen.correct, seen.charged) == ("₹76.00", "₹152.00", charged)
    assert seen.finding.startswith(finding)
    assert seen.error is error


def test_saving_stores_the_shipment_its_photo_and_its_charges(tmp_path: Path) -> None:
    store = AddedShipments(tmp_path / "shipments.sqlite3")
    ledger = tmp_path / "ledger.sqlite3"
    ref = save(store, ledger, kurta(charged_twice=True), JPG, datetime.now(UTC))
    assert ref == "SHP-100001"
    assert (store.photos / "shp-100001.jpg").is_file()
    entries = SimulatedLedger(ledger).get_entries("SHP-100001")
    assert [(e.type, str(e.amount.amount)) for e in entries] == [
        ("DEBIT", "190.00"),
        ("DEBIT", "190.00"),
    ]


def test_a_refused_photo_stores_nothing(tmp_path: Path) -> None:
    store = AddedShipments(tmp_path / "shipments.sqlite3")
    ledger = tmp_path / "ledger.sqlite3"
    with pytest.raises(ShipmentRejectedError, match="JPG or PNG"):
        save(store, ledger, kurta(), b"%PDF-1.7", datetime.now(UTC))
    assert store.all() == []
    assert not ledger.exists() or SimulatedLedger(ledger).get_entries("SHP-100001") == []
