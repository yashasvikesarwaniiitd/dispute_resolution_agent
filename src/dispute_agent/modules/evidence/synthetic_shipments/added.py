"""Shipments added from the console (ADR 0056).

The fourteen fixtures are fixed. This store holds the synthetic shipments a person adds on the
Raise a ticket screen, so a new kind of case can be tried without editing code. Each one is
served exactly as a fixture is: the library builds a `Fixture` from the stored row, and the
evidence source answers from it with the same three answers and no fourth. A field left empty
(no photo note, no photo) stays MISSING; nothing is invented for it.

Every amount is code's. The form gives sizes, weights, a zone and, optionally, a charge typed
in to stand for a billing mistake; the rate card works out what the charge should be. Stored
values are re-validated on read, so a row edited outside the console cannot reach a case in a
shape the form would have refused.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from dispute_agent.modules.evidence.synthetic_shipments.fixtures import (
    CARD,
    SELLER,
    Fixture,
    fixture,
)
from dispute_agent.modules.evidence.synthetic_shipments.generator import LedgerEntry, Shipment
from dispute_agent.packs.weight_dispute.calculator import Dimensions, calculate

if TYPE_CHECKING:
    from pathlib import Path

#: Statuses a shipment can have, as the pack's eligibility reads them.
STATUSES = ("DELIVERED", "RTO", "LOST", "CANCELLED")
REF = re.compile(r"^SHP-\d{6}$")
#: Added shipments are numbered from here, clear of the fixtures (SHP-000001 ...).
FIRST_ADDED = 100001
MAX_CM = Decimal(300)
MAX_GRAMS = 100_000
#: ₹1 to ₹1,00,000: a typed charge outside this is a slip, not a billing mistake to test.
MIN_CHARGE_PAISE, MAX_CHARGE_PAISE = 100, 10_000_000
MAX_NOTE = 300
MAX_QUANTITY = 50
MAX_PHOTO_BYTES = 5 * 1024 * 1024
#: The hosted demo's ceiling. A form that can be submitted in a loop needs one.
MAX_ADDED = 50
#: Image types by their first bytes, never by the name or the browser's claim.
PHOTO_TYPES = {b"\xff\xd8\xff": ("image/jpeg", "jpg"), b"\x89PNG\r\n\x1a\n": ("image/png", "png")}

SCHEMA = """
CREATE TABLE IF NOT EXISTS added_shipments (
  ref        TEXT PRIMARY KEY,
  body       TEXT NOT NULL,
  added_at   TEXT NOT NULL
)
"""


class ShipmentRejectedError(ValueError):
    """The form's values cannot make a shipment. The message says which, in plain words."""


@dataclass(frozen=True)
class NewShipment:
    """What the form sends. Sizes in centimetres, weights in grams, money in paise."""

    ref: str
    zone: str
    status: str
    declared: Dimensions
    declared_grams: int
    measured: Dimensions
    measured_grams: int
    #: A charge typed in to stand for a billing mistake. None: the rate card on the hub's
    #: measurement, which is a correct bill.
    charged_paise: int | None = None
    #: The same charge debited a second time, to try the duplicate-charge case.
    charged_twice: bool = False
    #: What the hub wrote about the parcel photo. People's words: outside text (I5).
    photo_note: str | None = None
    packaging: str = "box"
    #: A demo seller's identity (ADR 0072): one of their products and packagings, a quantity, a
    #: known hub and scanner, and when it was measured. Set, the shipment belongs to that seller
    #: and is compared with their real history; unset, it is the old stand-alone shipment.
    seller_id: str | None = None
    sku_id: str | None = None
    packaging_id: str | None = None
    quantity: int = 1
    hub_id: str | None = None
    scanner_id: str | None = None
    measured_at: datetime | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "zone": self.zone,
            "status": self.status,
            "declared": _dims_json(self.declared),
            "declared_grams": self.declared_grams,
            "measured": _dims_json(self.measured),
            "measured_grams": self.measured_grams,
            "charged_paise": self.charged_paise,
            "charged_twice": self.charged_twice,
            "photo_note": self.photo_note,
            "packaging": self.packaging,
            "seller_id": self.seller_id,
            "sku_id": self.sku_id,
            "packaging_id": self.packaging_id,
            "quantity": self.quantity,
            "hub_id": self.hub_id,
            "scanner_id": self.scanner_id,
            "measured_at": self.measured_at.isoformat() if self.measured_at else None,
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> NewShipment:
        return cls(
            ref=str(raw["ref"]),
            zone=str(raw["zone"]),
            status=str(raw["status"]),
            declared=Dimensions.of(*raw["declared"]),
            declared_grams=int(raw["declared_grams"]),
            measured=Dimensions.of(*raw["measured"]),
            measured_grams=int(raw["measured_grams"]),
            charged_paise=None if raw.get("charged_paise") is None else int(raw["charged_paise"]),
            charged_twice=bool(raw.get("charged_twice", False)),
            photo_note=raw.get("photo_note") or None,
            packaging=str(raw.get("packaging") or "box"),
            seller_id=raw.get("seller_id") or None,
            sku_id=raw.get("sku_id") or None,
            packaging_id=raw.get("packaging_id") or None,
            quantity=int(raw.get("quantity") or 1),
            hub_id=raw.get("hub_id") or None,
            scanner_id=raw.get("scanner_id") or None,
            measured_at=datetime.fromisoformat(raw["measured_at"])
            if raw.get("measured_at")
            else None,
        )


def _dims_json(d: Dimensions) -> list[str]:
    return [str(d.length_cm), str(d.width_cm), str(d.height_cm)]


def dimensions(length: object, width: object, height: object) -> Dimensions:
    """Centimetres from a form, each above 0 and at most 300, to the millimetre."""
    sides = []
    for name, value in (("length", length), ("width", width), ("height", height)):
        try:
            cm = Decimal(str(value)).quantize(Decimal("0.1"))
        except (InvalidOperation, ValueError) as bad:
            raise ShipmentRejectedError(f"The {name} must be a number of centimetres.") from bad
        if not Decimal(0) < cm <= MAX_CM:
            raise ShipmentRejectedError(f"The {name} must be above 0 and at most 300 cm.")
        sides.append(cm)
    return Dimensions.of(*sides)


def fixed_refs() -> frozenset[str]:
    """Every shipment the fixtures describe, cluster members included."""
    from dispute_agent.modules.evidence.synthetic_shipments.fixtures import (
        FIXTURES,
        cluster_members,
    )

    refs: set[str] = set()
    for name in FIXTURES:
        built = fixture(name)
        refs.add(built.shipment.ref)
        refs.update(member.ref for member in cluster_members(built))
    return frozenset(refs)


def check(new: NewShipment) -> None:
    """Raises ShipmentRejectedError naming the first value the form should not have sent."""
    if not REF.match(new.ref):
        raise ShipmentRejectedError("The shipment ID must look like SHP-100001.")
    if new.zone not in CARD.zone_names():
        raise ShipmentRejectedError(f"The zone must be one of {', '.join(CARD.zone_names())}.")
    if new.status not in STATUSES:
        raise ShipmentRejectedError("The status is not one the system knows.")
    for name, grams in (("declared", new.declared_grams), ("measured", new.measured_grams)):
        if isinstance(grams, bool) or not 0 < grams <= MAX_GRAMS:
            raise ShipmentRejectedError(f"The {name} weight must be 1 to 100,000 g.")
    for d in (new.declared, new.measured):
        if not all(Decimal(0) < side <= MAX_CM for side in _sides(d)):
            raise ShipmentRejectedError("Every side must be above 0 and at most 300 cm.")
    if new.charged_paise is not None and not (
        MIN_CHARGE_PAISE <= new.charged_paise <= MAX_CHARGE_PAISE
    ):
        raise ShipmentRejectedError("A typed charge must be ₹1 to ₹1,00,000.")
    if new.photo_note is not None and len(new.photo_note) > MAX_NOTE:
        raise ShipmentRejectedError("The photo note must be 300 characters or fewer.")
    if new.seller_id is not None:
        _check_identity(new)


def _check_identity(new: NewShipment) -> None:
    """A demo seller's shipment uses only what that seller and the network really have."""
    from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue

    seller = catalogue().seller(new.seller_id or "")
    if seller is None:
        raise ShipmentRejectedError("Choose one of the demo sellers.")
    if new.sku_id not in {p.sku_id for p in seller.products}:
        raise ShipmentRejectedError("Choose one of this seller's products.")
    if new.packaging_id not in {p.packaging_id for p in seller.packaging}:
        raise ShipmentRejectedError("Choose one of this seller's packagings.")
    if not 1 <= new.quantity <= MAX_QUANTITY:
        raise ShipmentRejectedError(f"The quantity must be 1 to {MAX_QUANTITY}.")
    network = {(r.hub_id, r.scanner_id) for s in catalogue().sellers for r in s.shipments}
    if (new.hub_id, new.scanner_id) not in network:
        raise ShipmentRejectedError("Choose a hub and one of its scanners.")
    if new.measured_at is None:
        raise ShipmentRejectedError("Give the date the hub measured the parcel.")


def _sides(d: Dimensions) -> tuple[Decimal, Decimal, Decimal]:
    return (d.length_cm, d.width_cm, d.height_cm)


def correct_paise(new: NewShipment) -> int:
    """The rate card on the hub's measurement: what a correct bill is."""
    return calculate(new.measured, new.measured_grams, new.zone, CARD, new.packaging).amount_paise


def as_fixture(new: NewShipment) -> Fixture:
    """The stored shipment in the shape every fixture has, so it is served the same way."""
    claim = new.ref.replace("SHP", "CLM")
    billed = new.charged_paise if new.charged_paise is not None else correct_paise(new)
    debits = [
        LedgerEntry(f"TXN-{new.ref}-{n}", new.ref, claim, "DEBIT", billed)
        for n in range(1, 3 if new.charged_twice else 2)
    ]
    identity = new.seller_id is not None
    shipment = Shipment(
        ref=new.ref,
        claim_ref=claim,
        seller_id=new.seller_id or SELLER,
        # Without a seller: its own SKU, so it never joins a fixture's cross-case cluster.
        sku_id=new.sku_id if identity and new.sku_id else f"SKU-{new.ref}",
        hub_id=new.hub_id if identity and new.hub_id else "HUB-01",
        scanner_id=new.scanner_id if identity and new.scanner_id else "SCN-01-1",
        zone=new.zone,
        status=new.status,
        declared=new.declared,
        declared_dead_grams=new.declared_grams,
        measured=new.measured,
        measured_dead_grams=new.measured_grams,
        packaging=new.packaging,
        billed_paise=billed,
        correct_paise=correct_paise(new),
        ledger=tuple(debits),
    )
    if not identity:
        return Fixture(name=f"added:{new.ref}", shipment=shipment, image_description=new.photo_note)
    return Fixture(
        name=f"added:{new.ref}",
        shipment=shipment,
        image_description=new.photo_note,
        seller_contact=_contact(new.seller_id or ""),
        quantity=new.quantity,
        packaging_id=new.packaging_id,
        measured_at=new.measured_at,
        comparable_history=_comparison(new),
    )


def _contact(seller_id: str) -> str:
    from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue
    from dispute_agent.modules.evidence.synthetic_shipments.generator import TEST_DOMAIN

    seller = catalogue().seller(seller_id)
    return f"seller+{seller.seller.contact_tag if seller else 'seller'}@{TEST_DOMAIN}"


def _comparison(new: NewShipment) -> dict[str, Any] | None:
    """The same authoritative comparison every catalogue shipment gets, against the seller's
    real history - computed by the unchanged `history.compare`. The added parcel is the one
    compared, never part of the history (ADR 0072)."""
    from dispute_agent.modules.evidence.synthetic_shipments.catalogue import _reading, catalogue
    from dispute_agent.packs.weight_dispute.history import Reading, compare

    seller = catalogue().seller(new.seller_id or "")
    if seller is None or new.measured_at is None:
        return None
    box = next((p for p in seller.packaging if p.packaging_id == new.packaging_id), None)
    current = Reading(
        ref=new.ref,
        seller_id=seller.seller.seller_id,
        sku_id=new.sku_id or "",
        packaging_id=new.packaging_id or "",
        quantity=new.quantity,
        measured_at=new.measured_at,
        sides=_sides(new.measured),
        grams=new.measured_grams,
        measurement_status="disputed",
    )
    return compare(
        current,
        [_reading(seller, row) for row in seller.shipments],
        declared_sides=_sides(new.declared),
        packaging_sides=(box.length_cm, box.width_cm, box.height_cm) if box else None,
    )


@dataclass
class AddedShipments:
    """The store. One SQLite file beside the case database; read on every lookup."""

    path: Path
    limit: int = MAX_ADDED
    _fixed: frozenset[str] = field(default_factory=frozenset, repr=False)

    def __post_init__(self) -> None:
        self._fixed = fixed_refs()

    @property
    def photos(self) -> Path:
        return self.path.parent / "photos"

    def _db(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        db.execute(SCHEMA)
        return db

    def add(self, new: NewShipment, added_at: str) -> Fixture:
        """Store one shipment and return it as served. Raises ShipmentRejectedError."""
        check(new)
        if new.ref in self._fixed:
            raise ShipmentRejectedError(f"{new.ref} is one of the built-in shipments.")
        with closing(self._db()) as db, db:
            (count,) = db.execute("SELECT COUNT(*) FROM added_shipments").fetchone()
            if count >= self.limit:
                raise ShipmentRejectedError(
                    f"This demo holds at most {self.limit} added shipments."
                )
            try:
                db.execute(
                    "INSERT INTO added_shipments VALUES (?,?,?)",
                    (new.ref, json.dumps(new.to_json()), added_at),
                )
            except sqlite3.IntegrityError as taken:
                raise ShipmentRejectedError(f"{new.ref} has already been added.") from taken
        return as_fixture(new)

    def get(self, ref: str) -> Fixture | None:
        if not self.path.is_file():
            return None
        with closing(self._db()) as db:
            row = db.execute("SELECT body FROM added_shipments WHERE ref=?", (ref,)).fetchone()
        return self._built(row[0]) if row else None

    def new(self, ref: str) -> NewShipment | None:
        """The stored form values of one shipment (its identity included), or None."""
        if not self.path.is_file():
            return None
        with closing(self._db()) as db:
            row = db.execute("SELECT body FROM added_shipments WHERE ref = ?", (ref,)).fetchone()
        if row is None:
            return None
        try:
            return NewShipment.from_json(json.loads(row[0]))
        except (KeyError, TypeError, ValueError):
            return None

    def all(self) -> list[Fixture]:
        if not self.path.is_file():
            return []
        with closing(self._db()) as db:
            rows = db.execute("SELECT body FROM added_shipments ORDER BY ref").fetchall()
        return [built for (body,) in rows if (built := self._built(body)) is not None]

    def next_ref(self) -> str:
        taken = {f.shipment.ref for f in self.all()} | self._fixed
        n = FIRST_ADDED
        while f"SHP-{n:06d}" in taken:
            n += 1
        return f"SHP-{n:06d}"

    def save_photo(self, ref: str, data: bytes) -> Path:
        """Keep the parcel photo as `photos/<ref>.<jpg|png>`. Raises ShipmentRejectedError."""
        if not REF.match(ref):
            raise ShipmentRejectedError("The shipment ID must look like SHP-100001.")
        kind = photo_kind(data)
        self.photos.mkdir(parents=True, exist_ok=True)
        for old in self.photos.glob(f"{ref.lower()}.*"):
            old.unlink()
        target = self.photos / f"{ref.lower()}.{kind}"
        target.write_bytes(data)
        return target

    def _built(self, body: str) -> Fixture | None:
        """A stored row as served, re-checked: a row the form would refuse is not served."""
        try:
            new = NewShipment.from_json(json.loads(body))
            check(new)
        except (ShipmentRejectedError, ValueError, KeyError, TypeError):
            return None
        return as_fixture(new)


def photo_kind(data: bytes) -> str:
    """ "jpg" or "png", by the file's first bytes. Raises ShipmentRejectedError otherwise."""
    if len(data) > MAX_PHOTO_BYTES:
        raise ShipmentRejectedError("The photo must be 5 MB or smaller.")
    kind = next((ext for magic, (_t, ext) in PHOTO_TYPES.items() if data.startswith(magic)), None)
    if kind is None:
        raise ShipmentRejectedError("The photo must be a JPG or PNG image.")
    return kind


def photo_in(folder: Path, ref: str) -> tuple[Path, str] | None:
    """A shipment's photo in one folder, with its media type, by the file's own first bytes."""
    for ext in ("jpg", "png"):
        path = folder / f"{ref.lower()}.{ext}"
        if path.is_file():
            with path.open("rb") as image:
                head = image.read(8)
            for magic, (media_type, _ext) in PHOTO_TYPES.items():
                if head.startswith(magic):
                    return path, media_type
    return None


__all__ = [
    "FIRST_ADDED",
    "MAX_ADDED",
    "STATUSES",
    "AddedShipments",
    "NewShipment",
    "ShipmentRejectedError",
    "as_fixture",
    "check",
    "correct_paise",
    "dimensions",
    "photo_in",
    "photo_kind",
]
