"""The seller catalogue: three synthetic sellers and their shipments, from YAML (ADR 0060).

`config/sellers/*.yaml` is the source of truth: the sellers, their products and packaging,
and every shipment - past ones (`role: history`) and the disputes the demo raises
(`role: dispute`). Each shipment is served to the agent exactly as a named fixture is, plus
one more evidence item: how its measurement compares with the seller's trusted past shipments
(`packs/weight_dispute/history.py`), computed here once, in code.

What the agent never sees from these files: the `demo_label` (a filter for people on the
seller page) and any outcome. A hub's answer is not stored with a shipment; the scenario's
`ops_finding` step supplies it from the findings bank, only when the hub answers.

Validated on load. A file that does not match the shape below raises at start-up instead of
reaching a case half-read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.modules.evidence.synthetic_shipments.fixtures import CARD, Fixture
from dispute_agent.modules.evidence.synthetic_shipments.generator import (
    TEST_DOMAIN,
    LedgerEntry,
    Shipment,
)
from dispute_agent.packs.weight_dispute.calculator import Dimensions, calculate
from dispute_agent.packs.weight_dispute.history import Reading, compare

_STRICT = ConfigDict(extra="forbid", frozen=True)


class SellerProfile(BaseModel):
    model_config = _STRICT
    seller_id: str
    business: str
    owner: str
    category: str
    contact_tag: str
    synthetic: Literal[True]


class Packaging(BaseModel):
    model_config = _STRICT
    packaging_id: str
    kind: str
    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal
    tare_grams: int


class Product(BaseModel):
    model_config = _STRICT
    sku_id: str
    name: str
    packaging_id: str
    quantity: int = Field(ge=1)
    item_grams: int


class Row(BaseModel):
    model_config = _STRICT
    ref: str
    role: Literal["history", "dispute"]
    sku_id: str
    packaging_id: str
    quantity: int = Field(ge=1)
    measured_at: datetime
    hub_id: str
    scanner_id: str
    zone: Literal["A", "B", "C", "D", "E"]
    status: str
    declared_cm: tuple[Decimal, Decimal, Decimal]
    declared_grams: int
    measured_cm: tuple[Decimal, Decimal, Decimal]
    measured_grams: int
    billed_paise: int
    measurement_status: Literal["trusted", "disputed", "corrected"]
    fixture: str | None = None
    demo_label: str | None = None
    duplicate_debit: bool = False


class SellerFile(BaseModel):
    model_config = _STRICT
    seller: SellerProfile
    packaging: tuple[Packaging, ...]
    products: tuple[Product, ...]
    shipments: tuple[Row, ...]


@dataclass(frozen=True)
class Catalogue:
    """Every seller file, plus the fixtures built from them."""

    sellers: tuple[SellerFile, ...]
    by_ref: dict[str, Fixture]
    by_name: dict[str, Fixture]

    def seller(self, seller_id: str) -> SellerFile | None:
        return next((s for s in self.sellers if s.seller.seller_id == seller_id), None)

    def owner_of(self, shipment_ref: str) -> SellerFile | None:
        built = self.by_ref.get(shipment_ref)
        return self.seller(built.shipment.seller_id) if built else None


def sellers_dir() -> Path:
    """`config/sellers`, found upwards from this file (the package is installed editable)."""
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "sellers"
        if candidate.is_dir():
            return candidate
    return Path.cwd() / "config" / "sellers"


@lru_cache(maxsize=1)
def catalogue() -> Catalogue:
    directory = sellers_dir()
    files = tuple(
        SellerFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    )
    by_ref: dict[str, Fixture] = {}
    by_name: dict[str, Fixture] = {}
    for seller in files:
        for built in _fixtures(seller):
            by_ref[built.shipment.ref] = built
            if built.name:
                by_name[built.name] = built
    return Catalogue(sellers=files, by_ref=by_ref, by_name=by_name)


def dispute_fixture(name: str) -> Fixture | None:
    """A catalogue dispute by its fixture name (`ck_tray_anomaly`, ...), or None."""
    return catalogue().by_name.get(name)


def _fixtures(seller: SellerFile) -> list[Fixture]:
    packaging = {p.packaging_id: p for p in seller.packaging}
    readings = [_reading(seller, row) for row in seller.shipments]
    built: list[Fixture] = []
    for row, reading in zip(seller.shipments, readings, strict=True):
        box = packaging.get(row.packaging_id)
        comparison = compare(
            reading,
            readings,
            declared_sides=row.declared_cm,
            packaging_sides=(box.length_cm, box.width_cm, box.height_cm) if box else None,
        )
        built.append(
            Fixture(
                name=row.fixture or row.ref,
                shipment=_shipment(seller, row, box.kind if box else "box"),
                seller_contact=f"seller+{seller.seller.contact_tag}@{TEST_DOMAIN}",
                quantity=row.quantity,
                packaging_id=row.packaging_id,
                measured_at=row.measured_at,
                comparable_history=comparison,
            )
        )
    return built


def _reading(seller: SellerFile, row: Row) -> Reading:
    return Reading(
        ref=row.ref,
        seller_id=seller.seller.seller_id,
        sku_id=row.sku_id,
        packaging_id=row.packaging_id,
        quantity=row.quantity,
        measured_at=row.measured_at,
        sides=row.measured_cm,
        grams=row.measured_grams,
        measurement_status=row.measurement_status,
    )


def _shipment(seller: SellerFile, row: Row, kind: str) -> Shipment:
    measured = Dimensions.of(*(str(v) for v in row.measured_cm))
    claim = row.ref.replace("SHP", "CLM")
    debits = [
        LedgerEntry(
            transaction_id=f"TXN-{row.ref}-{n}",
            shipment_ref=row.ref,
            claim_ref=claim,
            kind="DEBIT",
            amount_paise=row.billed_paise,
        )
        for n in range(1, 3 if row.duplicate_debit else 2)
    ]
    return Shipment(
        ref=row.ref,
        claim_ref=claim,
        seller_id=seller.seller.seller_id,
        sku_id=row.sku_id,
        hub_id=row.hub_id,
        scanner_id=row.scanner_id,
        zone=row.zone,
        status=row.status,
        declared=Dimensions.of(*(str(v) for v in row.declared_cm)),
        declared_dead_grams=row.declared_grams,
        measured=measured,
        measured_dead_grams=row.measured_grams,
        packaging=kind,
        billed_paise=row.billed_paise,
        correct_paise=calculate(measured, row.measured_grams, row.zone, CARD).amount_paise,
        ledger=tuple(debits),
    )


def describe(seller: SellerFile) -> dict[str, Any]:
    """The seller's public card for the screens: who they are, never anything about outcomes."""
    profile = seller.seller
    return {
        "seller_id": profile.seller_id,
        "business": profile.business,
        "owner": profile.owner,
        "category": profile.category,
        "contact_tag": profile.contact_tag,
    }


__all__ = ["Catalogue", "SellerFile", "catalogue", "describe", "dispute_fixture", "sellers_dir"]
