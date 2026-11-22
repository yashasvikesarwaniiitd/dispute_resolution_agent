"""The hub's answer, read from a comment on the agent's own Jira issue (card P3-07).

The hub team answers a remeasurement request by commenting on the issue. Code reads the
first line with strict patterns: no model, no guessing.

    Remeasured: 30 x 22 x 15 cm, 1.10 kg      -> remeasured, with the new size and weight
    Measurement confirmed / No fault found    -> no fault found: the original size stands
    The previous measurement was correct ...  -> the same, said the way a person says it

A comment that is none of these is not read as an answer here (None). The adapter still
passes it on as a reply with no measurement, so it reaches a person rather than vanishing.

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
#: The original measurement stands. The fixed forms, and the plain ways a person says it at
#: the start of a comment ("The previous measurement was correct, use that only." - the
#: owner's own words on WD-66, which the first version ignored). Anything after the phrase
#: is allowed, but a line with a negation anywhere is never a confirmation.
CONFIRMED = re.compile(
    r"^\s*(?:"
    r"measurement\s+confirmed|no\s+fault\s+found|confirmed"
    r"|(?:the\s+)?(?:previous|original|first|earlier|old|existing|hub'?s)?\s*"
    r"(?:measurement|reading|scan)s?\s+(?:is|was|were|are)\s+(?:correct|right|accurate|fine)"
    r"|(?:the\s+)?(?:previous|original|first|earlier|existing)\s+(?:measurement|reading|scan)\s+stands"
    r")\b",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(?:not|no\b(?!\s+fault)|never|incorrect|wrong|inaccurate)\b|n't", re.IGNORECASE
)

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
    if CONFIRMED.match(line) and not NEGATION.search(line):
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
