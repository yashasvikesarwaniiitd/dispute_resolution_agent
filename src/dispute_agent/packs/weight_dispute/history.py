"""How a disputed parcel compares with the seller's own past shipments (ADR 0060).

The question it answers: "is this measurement normal for this product, in this packaging, in
this quantity?" Pure functions over plain values - no I/O, no clock, no model - so the same
inputs always give the same answer, and the rules table and the AI read the same result.

What it is not: proof. A comparison is supporting evidence about a parcel's size. It never
proves a billing error and is never a reason for a credit; only code's recalculation of the
charge can do that (I9). "Trusted" means *eligible as a reference* (delivered, never disputed,
never corrected), not independently proven correct.

The method, fixed and approved before it was built (decision log, ADR 0060):

1. Missing size or weight on the disputed parcel -> `missing_evidence`.
2. Reference pool: same seller, SKU, packaging and quantity; measured before this parcel; not
   this parcel; `measurement_status == "trusted"`. Everything left out is counted by reason.
3. Sides are sorted longest first, so a box scanned on its side still matches.
4. Past readings above 1.5x or below 0.67x the pool's median volume are listed as outliers
   and kept out of the range (a second guard behind the status filter).
5. Verdict, first match wins: `not_comparable` (no pool, but the same SKU was shipped in other
   packaging or quantity), `insufficient_history` (fewer than 5), `anomalous` (volume, or scale
   weight, above 1.25x the median AND above the pool's maximum), otherwise `normal`.
6. Two flags, whatever the verdict: `declared_below_history` (the declared volume is below
   0.8x the median; only with a full pool) and `packaging_consistent` (the measured volume is
   within 0.75-1.25x the nominal volume of the packaging recorded at booking).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

MIN_REFERENCES = 5
ANOMALY_FACTOR = Decimal("1.25")
UNDER_DECLARED_FACTOR = Decimal("0.8")
OUTLIER_HIGH = Decimal("1.5")
OUTLIER_LOW = Decimal("0.67")
PACKAGING_LOW, PACKAGING_HIGH = Decimal("0.75"), Decimal("1.25")

VERDICTS = ("missing_evidence", "not_comparable", "insufficient_history", "anomalous", "normal")


@dataclass(frozen=True)
class Reading:
    """One parcel as measured. Sizes in centimetres, weight in grams."""

    ref: str
    seller_id: str
    sku_id: str
    packaging_id: str
    quantity: int
    measured_at: datetime
    sides: tuple[Decimal, Decimal, Decimal] | None
    grams: int | None
    measurement_status: str = "trusted"


def sorted_sides(sides: Sequence[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    a, b, c = sorted((Decimal(str(s)) for s in sides), reverse=True)
    return a, b, c


def volume(sides: Sequence[Decimal]) -> Decimal:
    a, b, c = sides
    return Decimal(str(a)) * Decimal(str(b)) * Decimal(str(c))


def compare(
    current: Reading,
    others: Sequence[Reading],
    *,
    declared_sides: Sequence[Decimal] | None = None,
    packaging_sides: Sequence[Decimal] | None = None,
) -> dict[str, Any]:
    """The comparison as a plain, JSON-safe dict (numbers as strings, I9)."""
    result: dict[str, Any] = {
        "verdict": "",
        "references": 0,
        "reference_refs": [],
        "outlier_refs": [],
        "excluded": {},
        "flags": {"declared_below_history": False, "packaging_consistent": None},
        "basis": "same seller, product, packaging and quantity; trusted readings before this one",
    }
    if current.sides is None or current.grams is None:
        result["verdict"] = "missing_evidence"
        return result

    now_sides = sorted_sides(current.sides)
    now_volume = volume(now_sides)
    result["current"] = _shape(now_sides, current.grams)
    if packaging_sides is not None:
        nominal = volume(sorted_sides(packaging_sides))
        if nominal > 0:
            ratio = now_volume / nominal
            result["flags"]["packaging_consistent"] = PACKAGING_LOW <= ratio <= PACKAGING_HIGH

    excluded: dict[str, int] = {}
    pool: list[Reading] = []
    other_packings: set[str] = set()
    for past in others:
        if past.ref == current.ref or past.seller_id != current.seller_id:
            continue
        if past.sku_id != current.sku_id:
            continue
        if past.packaging_id != current.packaging_id or past.quantity != current.quantity:
            other_packings.add(_difference(past, current))
            continue
        if past.measured_at >= current.measured_at:
            excluded["measured_later"] = excluded.get("measured_later", 0) + 1
            continue
        if past.measurement_status != "trusted":
            excluded[past.measurement_status] = excluded.get(past.measurement_status, 0) + 1
            continue
        if past.sides is None or past.grams is None:
            excluded["incomplete"] = excluded.get("incomplete", 0) + 1
            continue
        pool.append(past)
    result["excluded"] = excluded

    if not pool:
        if other_packings:
            result["verdict"] = "not_comparable"
            result["differs_by"] = sorted({p for d in other_packings for p in d.split("+")})
        else:
            result["verdict"] = "insufficient_history"
        return result

    volumes = {p.ref: volume(sorted_sides(p.sides or ())) for p in pool}
    mid = Decimal(str(median(volumes.values())))
    outliers = [
        p.ref
        for p in pool
        if volumes[p.ref] > mid * OUTLIER_HIGH or volumes[p.ref] < mid * OUTLIER_LOW
    ]
    kept = [p for p in pool if p.ref not in outliers]
    result["references"] = len(kept)
    result["reference_refs"] = [p.ref for p in kept]
    result["outlier_refs"] = outliers
    if len(kept) < MIN_REFERENCES:
        result["verdict"] = "insufficient_history"
        return result

    kept_sides = [sorted_sides(p.sides or ()) for p in kept]
    kept_volumes = [volumes[p.ref] for p in kept]
    kept_grams = [Decimal(p.grams or 0) for p in kept]
    median_sides = tuple(Decimal(str(median(s[i] for s in kept_sides))) for i in range(3))
    median_volume = Decimal(str(median(kept_volumes)))
    median_grams = Decimal(str(median(kept_grams)))
    result["typical"] = _shape(median_sides, median_grams)
    result["range"] = {
        "volume_cm3": [_cm3(min(kept_volumes)), _cm3(max(kept_volumes))],
        "grams": [_n(min(kept_grams)), _n(max(kept_grams))],
    }
    result["ratio_to_typical"] = {
        "volume": _n((now_volume / median_volume).quantize(Decimal("0.01"))),
        "grams": _n((Decimal(current.grams) / median_grams).quantize(Decimal("0.01"))),
    }
    bigger = now_volume > median_volume * ANOMALY_FACTOR and now_volume > max(kept_volumes)
    heavier = Decimal(current.grams) > median_grams * ANOMALY_FACTOR and Decimal(
        current.grams
    ) > max(kept_grams)
    result["verdict"] = "anomalous" if bigger or heavier else "normal"
    if declared_sides is not None:
        declared_volume = volume(sorted_sides(declared_sides))
        result["flags"]["declared_below_history"] = (
            declared_volume < median_volume * UNDER_DECLARED_FACTOR
        )
    return result


def _difference(past: Reading, current: Reading) -> str:
    parts = []
    if past.packaging_id != current.packaging_id:
        parts.append("packaging")
    if past.quantity != current.quantity:
        parts.append("quantity")
    return "+".join(parts)


def _shape(sides: Sequence[Decimal], grams: Decimal | int) -> dict[str, Any]:
    return {
        "sides_cm": [_n(s) for s in sides],
        "volume_cm3": _cm3(volume(sides)),
        "grams": _n(Decimal(grams)),
    }


def _cm3(value: Decimal) -> str:
    """A volume for display, to the whole cubic centimetre (decisions use the exact value)."""
    return _n(value.quantize(Decimal("1")))


def _n(value: Decimal) -> str:
    """A Decimal as plain text without trailing zeros ("19.1", "418")."""
    text = format(value.normalize(), "f")
    return text


__all__ = [
    "ANOMALY_FACTOR",
    "MIN_REFERENCES",
    "UNDER_DECLARED_FACTOR",
    "VERDICTS",
    "Reading",
    "compare",
    "sorted_sides",
    "volume",
]
