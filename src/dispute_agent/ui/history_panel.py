"""The "compared with past shipments" panel, shared by the seller and review screens (ADR 0060).

It shows what code computed - the counts, the usual size, the range, and which shipments it
rests on - in plain words. No percentages and no confidence: a comparison is supporting
evidence about a parcel's size, never proof. Every value is escaped (I5 is about outside text,
but these numbers reach HTML too).
"""

from __future__ import annotations

from typing import Any, Literal

from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue
from dispute_agent.ui.theme import escape

Audience = Literal["seller", "reviewer"]
TIMES = chr(0xD7)


def comparison_for(shipment_ref: str) -> dict[str, Any] | None:
    built = catalogue().by_ref.get(shipment_ref)
    return built.comparable_history if built else None


def owner_name(shipment_ref: str) -> str | None:
    owner = catalogue().owner_of(shipment_ref)
    return owner.seller.business if owner else None


def headline(comparison: dict[str, Any], audience: Audience) -> str:
    """The verdict as one plain sentence."""
    your = "your" if audience == "seller" else "this seller's"
    flags = comparison.get("flags") or {}
    verdict = comparison.get("verdict")
    if verdict == "anomalous":
        return (
            f"Much larger than {your} usual shipments of this product in this packaging. That is "
            "unusual, so it is a reason for the hub to check the scan, not proof of an error."
        )
    if verdict == "normal":
        line = f"In line with {your} usual shipments of this product in this packaging."
        if flags.get("declared_below_history"):
            line += " The declared size is smaller than these shipments usually measure."
        return line
    if verdict == "not_comparable":
        differs = " and ".join(comparison.get("differs_by") or ["packing"])
        return (
            f"Not comparable: past shipments of this product differed in {differs}, so they are "
            "not a fair baseline. This favours neither side."
        )
    if verdict == "insufficient_history":
        n = comparison.get("references", 0)
        return f"Too few past shipments to compare ({n}; at least 5 are needed)."
    return "No comparison: this parcel's measurements are missing."


def _sides(shape: dict[str, Any] | None) -> str:
    if not shape:
        return "-"
    return f" {TIMES} ".join(shape["sides_cm"]) + f" cm, {shape['grams']} g"


def panel_html(comparison: dict[str, Any] | None, audience: Audience) -> str:
    """The panel as HTML, or "" when this shipment has no comparison (named fixtures)."""
    if not comparison:
        return ""
    title = (
        "Compared with your past shipments"
        if audience == "seller"
        else "Compared with this seller's past shipments"
    )
    rows = ""
    if comparison.get("typical"):
        low, high = (comparison.get("range") or {}).get("volume_cm3", ["-", "-"])
        rows = (
            '<table class="calc hist"><tbody>'
            f'<tr><th scope="row">This parcel</th><td>{escape(_sides(comparison.get("current")))}</td></tr>'
            f'<tr><th scope="row">Usual (median)</th><td>{escape(_sides(comparison.get("typical")))}</td></tr>'
            f'<tr><th scope="row">Usual volume range</th><td>{escape(low)}-{escape(high)} cm³</td></tr>'
            f'<tr><th scope="row">Past shipments compared</th><td>{escape(comparison.get("references", 0))}</td></tr>'
            "</tbody></table>"
        )
    excluded = comparison.get("excluded") or {}
    reasons = {
        "disputed": "under dispute",
        "corrected": "re-measured by the hub",
        "measured_later": "measured later",
        "incomplete": "incomplete",
    }
    left_out = ", ".join(f"{n} {reasons.get(k, k)}" for k, n in sorted(excluded.items()))
    outliers = comparison.get("outlier_refs") or []
    notes = []
    if left_out:
        notes.append(f"Left out: {left_out}.")
    if outliers:
        notes.append(f"Unusual past readings kept out of the range: {', '.join(outliers)}.")
    refs = comparison.get("reference_refs") or []
    based = f"Based on: {', '.join(refs)}." if refs else ""
    small = "".join(f'<p class="muted hist-note">{escape(n)}</p>' for n in [*notes, based] if n)
    return (
        f'<div class="hist-panel"><p class="calc-title">{escape(title)}</p>'
        f'<p class="calc-verdict">{escape(headline(comparison, audience))}</p>'
        f"{rows}{small}</div>"
    )


__all__ = ["comparison_for", "headline", "owner_name", "panel_html"]
