"""What came back to the agent from outside: the hub's answer and the seller's reply.

One place, so the timeline and the person's card say the same thing. The figures are code's
reading of the answer; the words are shown exactly as recorded, as untrusted text (I5), and
never decide anything. A reply's quoted history (the "On ... wrote:" block and ">" lines a
mail client appends) is left out of what is shown: it is the agent's own email coming back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.evidence import EvidenceItem
    from dispute_agent.kernel.domain.models import Case

HUB = "ops.finding"
REPLY = "seller_reply"
#: Where a mail client starts quoting the message being replied to.
_QUOTE_START = re.compile(
    r"^\s*(?:On\s.+wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+|>.*)\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class CameBack:
    """One answer from outside: who sent it, what code read in it, and their words."""

    who: str  # "The hub" | "The seller"
    at: datetime
    summary: str
    words: tuple[str, ...]


def hub_answer(item: EvidenceItem) -> tuple[str, tuple[str, ...]]:
    """What the hub's answer says, in code's reading, and its words when code read none."""
    value = item.result.value or {}
    kind = value.get("kind")
    if kind == "remeasured" and all(k in value for k in ("length_cm", "width_cm", "height_cm")):
        size = f"{value['length_cm']} x {value['width_cm']} x {value['height_cm']} cm"
        grams = value.get("dead_grams")
        weight = f", {Decimal(str(grams)) / 1000:.2f} kg" if grams is not None else ""
        return f"Re-measured the parcel: {size}{weight}.", ()
    if kind == "no_fault_found":
        return "The hub stands by its measurement: no fault found.", ()
    if kind == "inspected":
        return (
            "The hub replied, but not with a measurement code can read. A person reads it.",
            tuple(u.text for u in item.result.untrusted),
        )
    return "", ()


def new_text(reply: str) -> str:
    """The reply above the quoted email it answers."""
    kept: list[str] = []
    for line in reply.replace("\r\n", "\n").split("\n"):
        if _QUOTE_START.match(line):
            break
        kept.append(line)
    return "\n".join(kept).strip() or reply.strip()


#: The reply reader's intents, as a person reads them (ADR 0054).
INTENT = {
    "provides_requested_evidence": "sent the evidence we asked for",
    "partial_evidence": "sent some of the evidence we asked for",
    "disputes_again_no_new_info": "disputes the charge again, with nothing new",
    "withdraws_dispute": "withdraws the dispute (a person confirms)",
    "asks_question": "asks a question",
    "irrelevant": "says nothing about this dispute",
    "contains_instruction_attempt": "tries to instruct the agent (a person checks)",
}
ITEM_STATUS = {"provided": "sent", "partial": "partly sent", "missing": "not sent"}


def seller_reply(item: EvidenceItem) -> tuple[str, tuple[str, ...]]:
    """What the agent read in the seller's reply, and their words.

    The reading is the reply reader's structured output: an intent and, per item we asked
    for, whether it arrived. It informs the next step; it never sets a figure (I9).
    """
    words = tuple(new_text(u.text) for u in item.result.untrusted)
    value = item.result.value or {}
    intent = value.get("intent")
    if item.result.status.value != "FOUND" or not isinstance(intent, str):
        return "The seller replied, and the agent could not read it. A person reads it.", words
    read = f"The agent read the reply: the seller {INTENT.get(intent, intent.replace('_', ' '))}."
    listed = value.get("requested_items_status") or []
    items = [
        f"{s.get('item', '')} - {ITEM_STATUS.get(str(s.get('status')), str(s.get('status')))}"
        for s in (listed if isinstance(listed, list | tuple) else [])
        if isinstance(s, dict)
    ]
    if items:
        read += " " + "; ".join(items) + "."
    if value.get("manipulation_flags"):
        read += " It flagged something that may be trying to steer the agent."
    return read, words


def came_back(work: Any, case: Case) -> tuple[CameBack, ...]:  # Any: a unit of work (reader)
    """Every answer from outside on this case, oldest first."""
    found: list[CameBack] = []
    for gather_round in range(1, case.gather_round + 1):
        try:
            items = work.evidence.bundle(case.id, gather_round).items
        except Exception:  # a round with nothing stored adds nothing
            continue
        for item in items:
            key = item.result.requirement_key
            if key == HUB and item.result.status.value == "FOUND":
                summary, words = hub_answer(item)
                if summary:
                    found.append(CameBack("The hub", item.result.retrieved_at, summary, words))
            elif key == REPLY:
                summary, words = seller_reply(item)
                found.append(CameBack("The seller", item.result.retrieved_at, summary, words))
    return tuple(found)


__all__ = ["CameBack", "came_back", "hub_answer", "new_text", "seller_reply"]
