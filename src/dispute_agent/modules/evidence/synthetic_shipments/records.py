"""Shipment records for the investigation tools, behind one small interface (ADR 0072).

The tools ask `ShipmentRecords` and never a database or a file, so the store behind it can move
(SQLite today via the YAML catalogue, PostgreSQL later) without touching a tool or the agent.

`CatalogueRecords` is today's implementation: the seller catalogue, plus the shipments people
add on the console. An added shipment can be looked up as "this parcel" - so it compares with
its seller's real history - but it is never part of anyone's history or of any aggregate: a test
parcel must not move the baseline it is judged against.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 - a dataclass field type
from decimal import Decimal  # noqa: TC003 - a dataclass field type
from typing import TYPE_CHECKING, Protocol

from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class ShipmentRecord:
    ref: str
    seller_id: str
    sku_id: str
    packaging_id: str
    quantity: int
    measured_at: datetime
    hub_id: str
    scanner_id: str
    declared_cm: tuple[Decimal, Decimal, Decimal]
    declared_grams: int
    measured_cm: tuple[Decimal, Decimal, Decimal]
    measured_grams: int
    measurement_status: str
    #: False for a console-added shipment: it can be looked up, never used as history.
    is_history: bool = True


@dataclass(frozen=True)
class Packaging:
    packaging_id: str
    kind: str
    sides_cm: tuple[Decimal, Decimal, Decimal]


class ShipmentRecords(Protocol):
    def record(self, shipment_ref: str) -> ShipmentRecord | None:
        """One shipment, history or added, or None when nothing describes it."""
        ...

    def seller_records(self, seller_id: str) -> Sequence[ShipmentRecord]:
        """Every history record of one seller. Never another seller's."""
        ...

    def all_history(self) -> Sequence[ShipmentRecord]:
        """Every seller's history, for aggregates only (counts, never records)."""
        ...

    def products(self, seller_id: str) -> dict[str, str]:
        """sku -> product name, for one seller."""
        ...

    def packagings(self, seller_id: str) -> dict[str, Packaging]: ...


class CatalogueRecords:
    """Implements ShipmentRecords over the seller catalogue and the console's added shipments."""

    def __init__(self, added: Callable[[str], ShipmentRecord | None] | None = None) -> None:
        self._added = added

    def record(self, shipment_ref: str) -> ShipmentRecord | None:
        for seller in catalogue().sellers:
            for row in seller.shipments:
                if row.ref == shipment_ref:
                    return _record(seller.seller.seller_id, row)
        return self._added(shipment_ref) if self._added else None

    def seller_records(self, seller_id: str) -> list[ShipmentRecord]:
        seller = catalogue().seller(seller_id)
        return [] if seller is None else [_record(seller_id, row) for row in seller.shipments]

    def all_history(self) -> list[ShipmentRecord]:
        return [
            _record(s.seller.seller_id, row) for s in catalogue().sellers for row in s.shipments
        ]

    def products(self, seller_id: str) -> dict[str, str]:
        seller = catalogue().seller(seller_id)
        return {} if seller is None else {p.sku_id: p.name for p in seller.products}

    def packagings(self, seller_id: str) -> dict[str, Packaging]:
        seller = catalogue().seller(seller_id)
        if seller is None:
            return {}
        return {
            p.packaging_id: Packaging(
                p.packaging_id, p.kind, (p.length_cm, p.width_cm, p.height_cm)
            )
            for p in seller.packaging
        }


def _record(seller_id: str, row: object) -> ShipmentRecord:
    r = row  # a catalogue Row
    return ShipmentRecord(
        ref=r.ref,  # type: ignore[attr-defined]
        seller_id=seller_id,
        sku_id=r.sku_id,  # type: ignore[attr-defined]
        packaging_id=r.packaging_id,  # type: ignore[attr-defined]
        quantity=r.quantity,  # type: ignore[attr-defined]
        measured_at=r.measured_at,  # type: ignore[attr-defined]
        hub_id=r.hub_id,  # type: ignore[attr-defined]
        scanner_id=r.scanner_id,  # type: ignore[attr-defined]
        declared_cm=tuple(r.declared_cm),  # type: ignore[attr-defined]
        declared_grams=r.declared_grams,  # type: ignore[attr-defined]
        measured_cm=tuple(r.measured_cm),  # type: ignore[attr-defined]
        measured_grams=r.measured_grams,  # type: ignore[attr-defined]
        measurement_status=r.measurement_status,  # type: ignore[attr-defined]
    )


def added_records(path: Path) -> Callable[[str], ShipmentRecord | None]:
    """A console-added shipment of a demo seller, as "this parcel" only (`is_history` False)."""
    from dispute_agent.modules.evidence.synthetic_shipments.added import AddedShipments

    store = AddedShipments(path)

    def lookup(ref: str) -> ShipmentRecord | None:
        new = store.new(ref)
        if new is None or new.seller_id is None or new.measured_at is None:
            return None
        sides = (new.measured.length_cm, new.measured.width_cm, new.measured.height_cm)
        declared = (new.declared.length_cm, new.declared.width_cm, new.declared.height_cm)
        return ShipmentRecord(
            ref=new.ref,
            seller_id=new.seller_id,
            sku_id=new.sku_id or "",
            packaging_id=new.packaging_id or "",
            quantity=new.quantity,
            measured_at=new.measured_at,
            hub_id=new.hub_id or "",
            scanner_id=new.scanner_id or "",
            declared_cm=declared,
            declared_grams=new.declared_grams,
            measured_cm=sides,
            measured_grams=new.measured_grams,
            measurement_status="disputed",
            is_history=False,
        )

    return lookup


__all__ = ["CatalogueRecords", "Packaging", "ShipmentRecord", "ShipmentRecords", "added_records"]
