"""The image check's verdict, in words a reviewer reads beside the photo (ADR 0049).

The verdict is stored as evidence item `parcel.image_check`: its closed choices are the item's
value, its observations are model-written untrusted text. Here the choices become words and
the observations are shown exactly as recorded, quoted, never as markup (I5). A check that
failed says so, so a person never mistakes "no verdict" for "nothing to see" (I4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.ui.theme import escape, pairs, recorded

if TYPE_CHECKING:
    from collections.abc import Iterable

    from dispute_agent.kernel.domain.evidence import EvidenceItem

#: The kernel's key for the verdict (kernel/orchestrator/image_check.py); repeated here so the
#: console does not import the orchestrator.
IMAGE_CHECK = "parcel.image_check"

COUNT = {
    "one": "One parcel",
    "more_than_one": "More than one parcel",
    "cannot_tell": "Cannot tell",
}
PACKAGING = {"sealed": "Sealed", "open": "Open", "damaged": "Damaged", "cannot_tell": "Cannot tell"}
LABEL = {"yes": "Yes", "no": "No", "cannot_tell": "Cannot tell"}
SIZE = {
    "cannot_be_judged": "Cannot be judged: nothing in view gives a scale",
    "looks_smaller_than_measured": "Looks smaller than the hub measured",
    "looks_consistent_with_measured": "Looks consistent with the hub's measurement",
}
CONFIDENCE = {"low": "Low", "medium": "Medium", "high": "High"}
#: Why a check failed, by the code after `image_check_failed:`.
FAILED = {
    "no_photo": "There is no photo on file for this shipment.",
    "invalid_model_output": "The model's answer broke the check's rules, so it was not used.",
    "guardrail_blocked": "The model service's safety screen blocked the request.",
    "transient": "The model service could not be reached.",
    "ambiguous": "The model service did not answer clearly.",
    "permanent": "The model service refused the request.",
    "auth": "The sign-in to the model service failed.",
    "config": "The photo check is not set up on this machine.",
    "module_unavailable": "The photo check is switched off.",
    "budget_exceeded": "The model spending cap was reached.",
}


@dataclass(frozen=True)
class PhotoCheck:
    ok: bool
    rows: tuple[tuple[str, str], ...] = ()
    observations: tuple[str, ...] = ()
    by: str = ""
    failure: str = ""

    @property
    def summary(self) -> str:
        if not self.ok:
            return f"The photo check did not give a verdict. {self.failure}"
        return ". ".join(f"{k}: {v.lower()}" for k, v in self.rows[:3]) + "."


def latest(items: Iterable[EvidenceItem]) -> PhotoCheck | None:
    """The newest photo check among these items, in words; None when there is none."""
    found = [i for i in items if i.result.requirement_key == IMAGE_CHECK]
    if not found:
        return None
    item = max(found, key=lambda i: i.gather_round)
    result = item.result
    if result.status.value != "FOUND":
        code = str(result.error_code or "").partition("image_check_failed:")[2]
        return PhotoCheck(ok=False, failure=FAILED.get(code, f"It failed ({code or 'unknown'})."))
    value = dict(result.value or {})
    rows = (
        ("Parcels", COUNT.get(str(value.get("parcel_count")), "Not recorded")),
        ("Packaging", PACKAGING.get(str(value.get("packaging")), "Not recorded")),
        ("Label visible", LABEL.get(str(value.get("label_visible")), "Not recorded")),
        ("Size", SIZE.get(str(value.get("size_from_photo")), "Not recorded")),
        ("Confidence", CONFIDENCE.get(str(value.get("confidence")), "Not recorded")),
    )
    by = f"{value.get('model_id', 'a model')}, prompt {value.get('prompt_name', '?')} " + str(
        value.get("prompt_version", "")
    )
    if str(value.get("route", "")).startswith("recorded:"):
        by += ", replayed from its saved evaluation answer (no live model call)"
    return PhotoCheck(
        ok=True, rows=rows, observations=tuple(u.text for u in result.untrusted), by=by.strip()
    )


def html(check: PhotoCheck) -> str:
    """The card's block: the verdict, the observations quoted, and who made it."""
    head = "<h3>Photo check</h3>"
    if not check.ok:
        return f'{head}<p class="reason">{escape(check.summary)}</p>'
    said = "\n".join(f"- {line}" for line in check.observations)
    return (
        head
        + '<p class="muted">Advice for you, from a vision model. It decides nothing.</p>'
        + pairs(check.rows)
        + (recorded("What it saw, exactly as recorded:", said) if said else "")
        + f'<p class="muted">By {escape(check.by)}.</p>'
    )


__all__ = ["IMAGE_CHECK", "PhotoCheck", "html", "latest"]
