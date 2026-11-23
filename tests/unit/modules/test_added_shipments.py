"""Shipments added from the console (ADR 0056): the store, the library and the photos."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.evidence import EvidenceRequirement
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.modules.evidence.synthetic_shipments.added import (
    AddedShipments,
    NewShipment,
    ShipmentRejectedError,
    as_fixture,
    dimensions,
)
from dispute_agent.modules.evidence.synthetic_shipments.library import SyntheticLibrarySource
from dispute_agent.modules.evidence.synthetic_shipments.photos import PhotoFolder
from dispute_agent.ui.seller import catalogue

if TYPE_CHECKING:
    from pathlib import Path

NOW = "2026-11-23T10:00:00+00:00"
JPG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def kurta(ref: str = "SHP-100001", **changes: object) -> NewShipment:
    """The kurta's sizes, billed one slab too many: ₹190 against a correct ₹152."""
    base = NewShipment(
        ref=ref,
        zone="B",
        status="DELIVERED",
        declared=dimensions(20, 15, 10),
        declared_grams=480,
        measured=dimensions(30, 22, 15),
        measured_grams=1100,
        charged_paise=19000,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


@pytest.fixture
def store(tmp_path: Path) -> AddedShipments:
    return AddedShipments(tmp_path / "shipments.sqlite3")


def test_an_added_shipment_is_kept_and_read_back(store: AddedShipments, tmp_path: Path) -> None:
    built = store.add(kurta(photo_note="single box"), NOW)
    again = AddedShipments(tmp_path / "shipments.sqlite3").get("SHP-100001")  # a fresh instance
    assert again == built
    s = built.shipment
    assert (s.claim_ref, s.seller_id, s.billed_paise, s.correct_paise) == (
        "CLM-100001",
        "SELL-0001",
        19000,
        15200,
    )
    assert [e.amount_paise for e in s.ledger] == [19000]
    assert built.image_description == "single box"
    assert [f.shipment.ref for f in store.all()] == ["SHP-100001"]


def test_without_a_typed_charge_the_rate_card_bills_it(store: AddedShipments) -> None:
    built = store.add(kurta(charged_paise=None, charged_twice=True), NOW)
    assert [e.amount_paise for e in built.shipment.ledger] == [15200, 15200]
    assert [e.transaction_id for e in built.shipment.ledger] == [
        "TXN-SHP-100001-1",
        "TXN-SHP-100001-2",
    ]


def test_the_next_reference_skips_what_is_taken(store: AddedShipments) -> None:
    assert store.next_ref() == "SHP-100001"
    store.add(kurta(), NOW)
    assert store.next_ref() == "SHP-100002"


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ({"ref": "SHIP-1"}, "must look like SHP-100001"),
        ({"ref": "SHP-000001"}, "one of the built-in shipments"),
        ({"zone": "Z"}, "zone must be one of"),
        ({"status": "SHIPPED"}, "status is not one"),
        ({"declared_grams": 0}, "declared weight must be"),
        ({"measured_grams": 100_001}, "measured weight must be"),
        ({"charged_paise": 50}, "typed charge must be"),
        ({"photo_note": "x" * 301}, "300 characters"),
    ],
)
def test_a_value_the_form_should_not_send_is_refused(
    store: AddedShipments, changes: dict[str, Any], says: str
) -> None:
    with pytest.raises(ShipmentRejectedError, match=says):
        store.add(kurta(**changes), NOW)
    assert store.all() == []


@pytest.mark.parametrize("sides", [(0, 15, 10), (20, 301, 10), ("twenty", 15, 10)])
def test_sizes_are_above_zero_and_at_most_300_cm(sides: tuple[object, object, object]) -> None:
    with pytest.raises(ShipmentRejectedError):
        dimensions(*sides)


def test_one_reference_once_and_a_ceiling_on_how_many(tmp_path: Path) -> None:
    store = AddedShipments(tmp_path / "s.sqlite3", limit=2)
    store.add(kurta(), NOW)
    with pytest.raises(ShipmentRejectedError, match="already been added"):
        store.add(kurta(), NOW)
    store.add(kurta("SHP-100002"), NOW)
    with pytest.raises(ShipmentRejectedError, match="at most 2"):
        store.add(kurta("SHP-100003"), NOW)


def test_a_row_edited_outside_the_console_is_not_served(store: AddedShipments) -> None:
    store.add(kurta(), NOW)
    with closing(sqlite3.connect(store.path)) as db, db:
        body = json.loads(db.execute("SELECT body FROM added_shipments").fetchone()[0])
        body["measured_grams"] = -5
        db.execute("UPDATE added_shipments SET body=?", (json.dumps(body),))
    assert store.get("SHP-100001") is None
    assert store.all() == []


def test_photos_are_known_by_their_bytes_not_their_name(store: AddedShipments) -> None:
    assert store.save_photo("SHP-100001", JPG).name == "shp-100001.jpg"
    assert store.save_photo("SHP-100001", PNG).name == "shp-100001.png"  # replaces the jpg
    assert [p.name for p in store.photos.iterdir()] == ["shp-100001.png"]
    with pytest.raises(ShipmentRejectedError, match="JPG or PNG"):
        store.save_photo("SHP-100001", b"GIF89a not a photo")
    with pytest.raises(ShipmentRejectedError, match="5 MB"):
        store.save_photo("SHP-100001", JPG + b"0" * (5 * 1024 * 1024))
    found = PhotoFolder(added=store.photos).photo("SHP-100001")
    assert found is not None and found.media_type == "image/png"
    assert PhotoFolder(added=store.photos).photo("SHP-100002") is None


def _facts(ref: str) -> CaseFacts:
    return CaseFacts(
        shipment_ref=ref,
        claim_ref=ref.replace("SHP", "CLM"),
        seller_ref="SELL-0001",
        seller_contact="seller+1@example.invalid",
        raised_at=datetime.now(UTC),
    )


def _fetch(source: SyntheticLibrarySource, ref: str, key: str) -> tuple[str, object]:
    got = source.fetch(
        CaseId("case-1"),
        _facts(ref),
        EvidenceRequirement(key=key, source="evidence", required=True),
    )
    return got.status.value, got.value


def test_the_library_serves_an_added_shipment_like_a_fixture(store: AddedShipments) -> None:
    source = SyntheticLibrarySource(added=store)
    assert _fetch(source, "SHP-100001", "shipment.status")[0] == "MISSING"
    store.add(kurta(), NOW)  # added while the service runs: served at once
    assert _fetch(source, "SHP-100001", "shipment.status") == ("FOUND", {"status": "DELIVERED"})
    status, ledger = _fetch(source, "SHP-100001", "ledger.entries")
    assert status == "FOUND" and isinstance(ledger, dict)
    assert [e["amount_paise"] for e in ledger["entries"]] == [19000]
    # No photo note was given: missing, never invented.
    assert _fetch(source, "SHP-100001", "shipment.image_description")[0] == "MISSING"
    assert "SHP-100001" in source.shipments()
    assert source.keys_for("SHP-100001")["sku"] == "SKU-SHP-100001"
    # The fixtures are untouched.
    assert _fetch(source, "SHP-000001", "shipment.status")[1] == {"status": "DELIVERED"}
    assert _fetch(source, "SHP-999999", "shipment.status")[0] == EvidenceStatus.MISSING.value


def test_the_seller_screen_lists_added_shipments_with_code_s_amounts(
    store: AddedShipments,
) -> None:
    store.add(kurta(), NOW)
    listed = {s.ref: s for s in catalogue(store.path)}
    assert "SHP-000001" in listed  # the fixtures stay
    added = listed["SHP-100001"]
    assert (added.charged, added.on_declared, added.over_card) == ("₹190.00", "₹76.00", "₹38.00")
    assert "SHP-100001" not in {s.ref for s in catalogue()}


def test_as_fixture_is_what_the_store_serves(store: AddedShipments) -> None:
    assert store.add(kurta(), datetime.now(UTC).isoformat()) == as_fixture(kurta())
