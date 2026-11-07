"""The evidence a seller may be asked for: a closed list, worded once (ADR 0043).

The investigator chooses items from this list; it never words them. The wording reaches the
seller as written here, through the evidence-request and reminder templates.

Why closed: an item the model words itself is free text in a customer message. The first full
live run (2026-11-07) showed the cost - the model asked for "two photos", R09 refused the
draft (a number word cannot be checked against a fact, MD-F1), and the case went to a person.
Digits would not help either: every digit a seller reads must trace to a stored fact, and "2"
does not. So the items carry no numbers at all, and no model-written text reaches the seller
at this step.

`prompts/case_investigator/v2/schema.json` lists exactly these strings as its enum; a test
fails the build if the two ever disagree. Adding an item means a new prompt version.
"""

from __future__ import annotations

EVIDENCE_ITEMS: tuple[str, ...] = (
    "Photo of the packed box beside a tape measure",
    "A photo of the packed parcel on a scale",
    "Packaging dimensions at dispatch",
    "Photo of the shipping label on the packed box",
    "The product invoice showing the item shipped",
    "Photo of the item before it was packed",
)

__all__ = ["EVIDENCE_ITEMS"]
