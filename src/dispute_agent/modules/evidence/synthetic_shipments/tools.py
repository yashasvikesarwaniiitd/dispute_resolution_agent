"""Two investigation tools over shipment records (ADR 0071, ADR 0072).

Both read `ShipmentRecords` only (records.py), so the store behind them can change without a
tool changing. Every figure is computed here and written as a string (I9). Only shipments
measured **before** this case's parcel count: a tool never shows the agent the future.

- `search_past_shipments`: this seller's own records, filtered as the agent asks. Returns compact
  records, statistics over the matches, the filter values that exist, and whether the query is
  the authoritative comparison's basis or supplementary context. The seller is the case's,
  set by code; there is no argument to change it (ADR 0058).
- `get_scanner_statistics`: how this parcel's scanner (or hub) has measured parcels against
  the others, across every seller - as counts only, over fixed windows, and suppressed below
  the aggregation thresholds, so no other seller's shipment can be inferred.

The authoritative comparison (`packs/weight_dispute/history.compare`) is not called or changed
here: it stays in the briefing, and the fallback rules read it there.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.tools import ToolOutput, ToolSpec
from dispute_agent.modules.evidence.synthetic_shipments.records import (
    CatalogueRecords,
    ShipmentRecord,
    ShipmentRecords,
)
from dispute_agent.packs.weight_dispute.history import (
    ANOMALY_FACTOR,
    MIN_REFERENCES,
    sorted_sides,
    volume,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from dispute_agent.kernel.domain.tools import ToolContext

#: Records per call: enough to read a pattern, small enough to fit the loop's result cap.
MAX_RECORDS = 10
#: Cross-seller aggregates are shown only above both thresholds; below, "insufficient_data".
MIN_READINGS = 5
MIN_SELLERS = 3
#: The scanner windows: fixed, anchored on this parcel, never chosen by the agent - so two
#: calls can never be subtracted to isolate a few readings.
WINDOWS = (("last_60_days", 60), ("last_365_days", 365))


class PastShipmentsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: str | None = Field(
        default=None, description="A product of this seller; default this parcel's."
    )
    packaging_id: str = Field(
        default="any", description="A packaging id of this seller (not its name), or 'any'."
    )
    quantity: int | Literal["any"] = Field(default="any", description="Items per parcel, or 'any'.")
    trusted_only: bool = Field(
        default=True, description="Leave out disputed and corrected readings."
    )
    date_from: date | None = None
    date_to: date | None = None
    hub_id: str | None = None
    scanner_id: str | None = None
    limit: int = Field(default=8, ge=1, le=MAX_RECORDS, description="Records shown, newest first.")


class ScannerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Literal["scanner", "hub"] = Field(
        default="scanner",
        description="scanner: this scanner against the hub's others; hub: this hub against all others.",
    )


# -- shared helpers ---------------------------------------------------------------------------


def _vol(sides: Iterable[Decimal]) -> Decimal:
    return volume(sorted_sides(tuple(sides)))


def _ratio(record: ShipmentRecord) -> Decimal:
    declared = _vol(record.declared_cm)
    return _vol(record.measured_cm) / declared if declared else Decimal(0)


def _cm(sides: Iterable[Decimal]) -> str:
    return " x ".join(f"{Decimal(s).normalize():f}" for s in sides)


def _n(value: Decimal, places: str = "0.01") -> str:
    return f"{value.quantize(Decimal(places)):f}"


def _this(records: ShipmentRecords, ctx: ToolContext) -> ShipmentRecord | None:
    found = records.record(ctx.facts.shipment_ref)
    # The case's own seller, from code: a record of another seller is never "this parcel".
    return found if found is not None and found.seller_id == ctx.facts.seller_ref else None


# -- search_past_shipments ---------------------------------------------------------------------


class PastShipmentsTool:
    """Implements InvestigationTool."""

    spec = ToolSpec(
        name="search_past_shipments",
        description=(
            "This seller's own shipments measured before this parcel, filtered by product, "
            "packaging, quantity, trusted-only, dates, hub or scanner. Returns compact records, "
            "statistics over the matches (sample size, typical size and weight, this parcel "
            "against them), the filter values that exist, and whether the query is the "
            "authoritative comparison's basis or supplementary context. The comparison verdict is "
            "already in the briefing: search when the records or another slice could change the decision."
        ),
        input_schema=PastShipmentsArgs.model_json_schema(),
    )

    def __init__(self, records: ShipmentRecords | None = None) -> None:
        self._records = records or CatalogueRecords()

    def accepted_values(self, ctx: ToolContext) -> str:
        """The ids this case's search accepts, in one line (the same lists `run` checks)."""
        current = _this(self._records, ctx)
        if current is None:
            return ""
        accepted = _accepted(_available(self._before(current), self._records, current.seller_id))
        mine = {"sku_id": current.sku_id, "packaging_id": current.packaging_id}
        return (
            "; ".join(
                f"{name}: "
                + ", ".join(
                    v + (" = this parcel" if v.split(" ")[0] == mine.get(name) else "")
                    for v in values
                )
                for name, values in accepted.items()
            )
            + f"; limit: 1-{MAX_RECORDS}"
        )

    def _before(self, current: ShipmentRecord) -> list[ShipmentRecord]:
        return [
            r
            for r in self._records.seller_records(current.seller_id)
            if r.is_history and r.ref != current.ref and r.measured_at < current.measured_at
        ]

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        asked = PastShipmentsArgs.model_validate(dict(args))
        current = _this(self._records, ctx)
        if current is None:
            return ToolOutput(tool=self.spec.name, ok=False, error="no_history_for_this_shipment")
        before = self._before(current)
        available = _available(before, self._records, current.seller_id)
        sku = asked.sku_id or current.sku_id
        bad = _unknown_value(asked, sku, available)
        if bad:
            # The accepted values of the one wrong argument, not every filter: enough to fix it.
            return ToolOutput(
                tool=self.spec.name,
                ok=False,
                error=f"unknown_{bad}",
                data={
                    "problem": f"{bad} is not one of this seller's; use an id below, not a name",
                    "accepted": _accepted(available)[bad],
                },
            )
        matched = [r for r in before if _matches(r, asked, sku)]
        matched.sort(key=lambda r: r.measured_at, reverse=True)
        differs = _differs(asked, sku, current)
        trusted = [r for r in matched if r.measurement_status == "trusted"]
        return ToolOutput(
            tool=self.spec.name,
            ok=True,
            data={
                "query": {**asked.model_dump(mode="json", exclude={"limit"}), "sku_id": sku},
                "basis": "authoritative_basis" if not differs else "supplementary",
                "differs_from_authoritative": differs,
                "matched": len(matched),
                "shown": min(len(matched), asked.limit),
                "stats": _stats(trusted, current),
                "limitations": _limitations(differs, len(trusted)),
                "records": [_compact(r) for r in matched[: asked.limit]],
                "available_filters": available,
            },
        )


def _matches(r: ShipmentRecord, asked: PastShipmentsArgs, sku: str) -> bool:
    day = r.measured_at.date()
    return (
        r.sku_id == sku
        and asked.packaging_id in ("any", r.packaging_id)
        and asked.quantity in ("any", r.quantity)
        and (not asked.trusted_only or r.measurement_status == "trusted")
        and (asked.date_from is None or day >= asked.date_from)
        and (asked.date_to is None or day <= asked.date_to)
        and asked.hub_id in (None, r.hub_id)
        and asked.scanner_id in (None, r.scanner_id)
    )


def _differs(asked: PastShipmentsArgs, sku: str, current: ShipmentRecord) -> list[str]:
    """How this query differs from the authoritative comparison's basis (same product,
    packaging and quantity, trusted only, every earlier date, any hub and scanner)."""
    out = []
    if sku != current.sku_id:
        out.append(f"other product: {sku}")
    if asked.packaging_id != current.packaging_id:
        out.append(f"packaging: {asked.packaging_id}")
    if asked.quantity != current.quantity:
        out.append(f"quantity: {asked.quantity}")
    if not asked.trusted_only:
        out.append("includes disputed and corrected readings (statistics still use trusted only)")
    if asked.date_from or asked.date_to:
        out.append(f"dates: {asked.date_from or 'start'} to {asked.date_to or 'this parcel'}")
    if asked.hub_id or asked.scanner_id:
        out.append(f"location: {asked.hub_id or ''} {asked.scanner_id or ''}".strip())
    return out


def _limitations(differs: list[str], trusted: int) -> list[str]:
    notes = []
    if differs:
        notes.append(
            "Supplementary context, not the authoritative baseline: it cannot prove a billing "
            "error, authorise a credit or replace the comparison in the briefing."
        )
    if trusted < MIN_REFERENCES:
        notes.append(
            f"Only {trusted} trusted matches (fewer than {MIN_REFERENCES}): too few to call typical."
        )
    return notes


def _stats(trusted: Sequence[ShipmentRecord], current: ShipmentRecord) -> dict[str, Any]:
    """Descriptive statistics over the trusted matches. No verdict words: those belong to the
    authoritative comparison alone."""
    stats: dict[str, Any] = {"sample_size": len(trusted)}
    if len(trusted) < MIN_REFERENCES:
        return stats
    sides = [sorted_sides(r.measured_cm) for r in trusted]
    volumes = [_vol(r.measured_cm) for r in trusted]
    typical_volume = Decimal(str(median(volumes)))
    stats.update(
        typical_measured_cm=_cm(
            Decimal(str(median(s[i] for s in sides))).quantize(Decimal("0.1")) for i in range(3)
        ),
        typical_measured_grams=str(int(median(r.measured_grams for r in trusted))),
        volume_cm3_range=[_n(min(volumes), "1"), _n(max(volumes), "1")],
        this_parcel_volume_to_typical=_n(_vol(current.measured_cm) / typical_volume)
        if typical_volume
        else None,
        this_parcel_grams_to_typical=_n(
            Decimal(current.measured_grams)
            / Decimal(str(median(r.measured_grams for r in trusted)))
        ),
        share_measured_over_1_25x_declared=_n(
            Decimal(sum(1 for r in trusted if _ratio(r) > ANOMALY_FACTOR)) / len(trusted)
        ),
        by_packaging=dict(sorted(Counter(r.packaging_id for r in trusted).items())),
        by_quantity={str(q): n for q, n in sorted(Counter(r.quantity for r in trusted).items())},
    )
    return stats


def _compact(r: ShipmentRecord) -> dict[str, Any]:
    """One record, compact (audit AUD-14): the product is the query's, so it is not repeated."""
    return {
        "ref": r.ref,
        "on": r.measured_at.date().isoformat(),
        "packaging": r.packaging_id,
        "qty": r.quantity,
        "scanner": r.scanner_id,
        "declared": f"{_cm(r.declared_cm)} cm {r.declared_grams} g",
        "measured": f"{_cm(r.measured_cm)} cm {r.measured_grams} g",
        "status": r.measurement_status,
    }


def _available(
    before: Sequence[ShipmentRecord], records: ShipmentRecords, seller_id: str
) -> dict[str, Any]:
    names, boxes = records.products(seller_id), records.packagings(seller_id)
    per_sku, per_box = Counter(r.sku_id for r in before), Counter(r.packaging_id for r in before)
    days = sorted(r.measured_at.date() for r in before)
    return {
        "products": [
            {"sku_id": s, "name": names.get(s, s), "records": n} for s, n in sorted(per_sku.items())
        ],
        "packagings": [
            {
                "packaging_id": p,
                "kind": boxes[p].kind if p in boxes else "",
                "nominal_cm": _cm(boxes[p].sides_cm) if p in boxes else "",
                "records": n,
            }
            for p, n in sorted(per_box.items())
        ],
        "quantities": sorted({r.quantity for r in before}),
        "hubs": sorted({r.hub_id for r in before}),
        "scanners": sorted({r.scanner_id for r in before}),
        "measured_between": [days[0].isoformat(), days[-1].isoformat()] if days else [],
    }


def _accepted(available: dict[str, Any]) -> dict[str, list[str]]:
    """The values each id argument accepts, as `_unknown_value` checks them."""
    return {
        "sku_id": [p["sku_id"] for p in available["products"]],
        "packaging_id": ["any"]
        + [
            f"{p['packaging_id']} ({p['kind']})" if p["kind"] else p["packaging_id"]
            for p in available["packagings"]
        ],
        "quantity": ["any"] + [str(q) for q in available["quantities"]],
        "hub_id": list(available["hubs"]),
        "scanner_id": list(available["scanners"]),
    }


def _unknown_value(asked: PastShipmentsArgs, sku: str, available: dict[str, Any]) -> str | None:
    if sku not in {p["sku_id"] for p in available["products"]}:
        return "sku_id"
    if asked.packaging_id != "any" and asked.packaging_id not in {
        p["packaging_id"] for p in available["packagings"]
    }:
        return "packaging_id"
    if asked.hub_id and asked.hub_id not in available["hubs"]:
        return "hub_id"
    if asked.scanner_id and asked.scanner_id not in available["scanners"]:
        return "scanner_id"
    return None


# -- get_scanner_statistics --------------------------------------------------------------------


class ScannerStatisticsTool:
    """Implements InvestigationTool."""

    spec = ToolSpec(
        name="get_scanner_statistics",
        description=(
            "How the scanner that measured this parcel (level 'scanner') or its whole hub (level "
            "'hub') has measured parcels before it, against the hub's other scanners or the other "
            "hubs: how often a reading exceeded 1.25x the declared volume, the typical measured-to-"
            "declared ratio, and how often readings were later corrected or disputed, over the last "
            "60 and 365 days. Counts across all sellers; a group too small to share safely is "
            "'insufficient_data'. Use it to tell a scanner or hub fault from a parcel that is simply big."
        ),
        input_schema=ScannerArgs.model_json_schema(),
    )

    def __init__(self, records: ShipmentRecords | None = None) -> None:
        self._records = records or CatalogueRecords()

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        asked = ScannerArgs.model_validate(dict(args))
        current = _this(self._records, ctx)
        if current is None:
            return ToolOutput(tool=self.spec.name, ok=False, error="no_record_for_this_shipment")
        every = [r for r in self._records.all_history() if r.is_history and r.ref != current.ref]
        labels = (
            ("this_scanner", "other_scanners_at_hub")
            if asked.level == "scanner"
            else ("this_hub", "other_hubs")
        )

        def group(r: ShipmentRecord) -> str | None:
            if asked.level == "hub":
                return labels[0] if r.hub_id == current.hub_id else labels[1]
            if r.scanner_id == current.scanner_id:
                return labels[0]
            return labels[1] if r.hub_id == current.hub_id else None

        windows = {}
        for label, days in WINDOWS:
            start = current.measured_at - timedelta(days=days)
            window = [r for r in every if start <= r.measured_at < current.measured_at]
            windows[label] = {g: _aggregate([r for r in window if group(r) == g]) for g in labels}
        return ToolOutput(
            tool=self.spec.name,
            ok=True,
            data={
                "level": asked.level,
                "scanner_id": current.scanner_id,
                "hub_id": current.hub_id,
                "this_parcel_measured_to_declared_volume": _n(_ratio(current)),
                "windows": windows,
                "thresholds": {"min_readings": MIN_READINGS, "min_sellers": MIN_SELLERS},
            },
        )


def _aggregate(group: Sequence[ShipmentRecord]) -> dict[str, Any] | str:
    """Counts only, and only when enough readings from enough sellers stand behind them."""
    if len(group) < MIN_READINGS or len({r.seller_id for r in group}) < MIN_SELLERS:
        return "insufficient_data"
    ratios = [_ratio(r) for r in group]
    return {
        "readings": len(group),
        "share_over_1_25x_declared": _n(
            Decimal(sum(1 for x in ratios if x > ANOMALY_FACTOR)) / len(group)
        ),
        "median_measured_to_declared_volume": _n(Decimal(str(median(ratios)))),
        "share_corrected_or_disputed": _n(
            Decimal(sum(1 for r in group if r.measurement_status != "trusted")) / len(group)
        ),
    }


__all__ = ["MIN_READINGS", "MIN_SELLERS", "PastShipmentsTool", "ScannerStatisticsTool"]
