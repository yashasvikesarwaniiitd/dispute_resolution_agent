"""What the image check is told about a parcel's size (ADR 0049).

The declared and measured size as the round's evidence recorded them, rendered as text for
the prompt's case facts ("20 x 15 x 10 cm, 480 g"). Copied, never computed or converted (I9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.packs.weight_dispute.pack import DECLARED, IMAGE, MEASURED

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.evidence import EvidenceResult

#: The key whose presence means "the hub flagged the photo": the row B5 reads.
PHOTO_FLAG = IMAGE


def size_facts(results: Sequence[EvidenceResult]) -> tuple[str, str]:
    """(declared, measured) as recorded, or "not recorded" for either that is missing."""
    found = {r.requirement_key: r.value for r in results if r.status is EvidenceStatus.FOUND}
    return _render(found.get(DECLARED)), _render(found.get(MEASURED))


def _render(value: object) -> str:
    if not isinstance(value, dict):
        return "not recorded"
    sides = [value.get(k) for k in ("length_cm", "width_cm", "height_cm")]
    if not all(isinstance(s, str) for s in sides):
        return "not recorded"
    text = " x ".join(str(s) for s in sides) + " cm"
    grams = value.get("dead_grams")
    return f"{text}, {grams} g" if isinstance(grams, int) else text


__all__ = ["PHOTO_FLAG", "size_facts"]
