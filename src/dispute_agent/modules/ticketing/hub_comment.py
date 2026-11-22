"""The hub's answer, read from a comment on the agent's own Jira issue (card P3-07).

The hub team answers a remeasurement request by commenting on the issue in one of two fixed
forms. Code reads them with a strict pattern: no model, no guessing. A comment that does not
match exactly is not an answer, and is ignored (it stays in Jira for people to read).

    Remeasured: 30 x 22 x 15 cm, 1.10 kg    -> remeasured, with the new size and weight
    Measurement confirmed                   -> no fault found: the original size stands
    No fault found                          -> the same

Only the first line counts, so a person may add a note below it. Sizes are centimetres,
weights kilograms or grams; the figures are Decimals and grams are an int, never floats (I9).
A figure outside what a parcel can be is refused rather than trusted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

NUMBER = r"(\d{1,4}(?:\.\d{1,2})?)"
#: Between two sides: the letter x, the multiplication sign (chr 215, kept out of the source
#: as a literal so it cannot be mistaken for an x), or *.
BY = r"\s*[x" + chr(215) + r"*]\s*"
REMEASURED = re.compile(
    r"^\s*re-?measured\s*:\s*"
    + NUMBER
    + BY
    + NUMBER
    + BY
    + NUMBER
    + r"\s*cm\s*[,;]?\s*"
    + NUMBER
    + r"\s*(kg|g)\s*\.?\s*$",
    re.IGNORECASE,
)
CONFIRMED = re.compile(r"^\s*(measurement\s+confirmed|no\s+fault\s+found)\s*\.?\s*$", re.IGNORECASE)

#: What a parcel can plausibly be. A figure outside this is a typo or worse, never a fact.
MAX_SIDE_CM = Decimal(300)
MAX_GRAMS = 100_000


@dataclass(frozen=True)
class HubAnswer:
    """One parsed answer. `measured` uses the evidence keys the pack reads."""

    kind: str  # "remeasured" | "no_fault_found"
    measured: dict[str, Decimal | int] = field(default_factory=dict)


def parse(comment: str) -> HubAnswer | None:
    """The hub's answer in a comment, or None when the comment is not one."""
    first = (comment or "").strip().splitlines()
    if not first:
        return None
    line = first[0]
    if CONFIRMED.match(line):
        return HubAnswer(kind="no_fault_found")
    found = REMEASURED.match(line)
    if found is None:
        return None
    try:
        sides = [Decimal(found.group(i)) for i in (1, 2, 3)]
        weight = Decimal(found.group(4))
    except InvalidOperation:
        return None
    grams = int(weight * 1000) if found.group(5).lower() == "kg" else int(weight)
    if any(s <= 0 or s > MAX_SIDE_CM for s in sides) or not 0 < grams <= MAX_GRAMS:
        return None
    return HubAnswer(
        kind="remeasured",
        measured={
            "length_cm": sides[0],
            "width_cm": sides[1],
            "height_cm": sides[2],
            "dead_grams": grams,
        },
    )


__all__ = ["HubAnswer", "parse"]
