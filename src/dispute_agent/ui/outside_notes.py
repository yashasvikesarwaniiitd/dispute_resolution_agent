"""What people outside the system wrote on a case, for the reviewer (ADR 0061).

Hub comments, seller emails, shipment notes and the dispute statement reach the agent as
untrusted text (I5): data, never instructions. A reviewer approving a credit must be able to
read them too, with where each one came from and whether the input screen found an attack
shape in it - a hub note that asks for money is the hub's claim, not a figure anything uses.

Every text is escaped. The screen verdict is the rules-based screen run on the stored text,
the same deterministic check the agent's round ran; it can only add caution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen
from dispute_agent.ui.theme import escape

if TYPE_CHECKING:
    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork

#: Where a text came from, in a reviewer's words.
SOURCE = {
    "jira_comment": "Hub comment (Jira)",
    "seller_email": "Seller email",
    "dispute_form": "Seller's dispute statement",
    "shipment_note": "Shipment note (hub)",
    "image_check": "Photo check",
}
#: What a screen flag means, in a reviewer's words.
FLAG_WORDS = {
    "approval_bypass": "asks to skip or get round a person's review",
    "instruction_like": "reads like an instruction to the agent",
    "markup_like": "contains markup",
    "hidden_text": "contains hidden text",
    "encoded_payload": "contains an encoded payload",
    "recipient_change": "asks to send something elsewhere",
    "number_injection": "offers a figure for the system to use",
    "fabricated_reference": "cites a reference that does not exist",
    "truncated_input": "was cut short",
}
MAX_CHARS = 600


@dataclass(frozen=True)
class Note:
    source: str
    reference: str
    text: str
    flags: tuple[str, ...]


def notes(work: SqliteUnitOfWork, case_id: str) -> list[Note]:
    """The case's outside texts in its latest evidence round, each with its screen verdict."""
    from dispute_agent.kernel.domain.ids import CaseId

    case = work.cases.get(CaseId(case_id))
    found: list[Note] = []
    seen: set[str] = set()
    bundle = work.evidence.bundle(case.id, case.gather_round)
    for item in bundle.items:
        for text in item.result.untrusted:
            if not text.text.strip() or text.text in seen:
                continue
            seen.add(text.text)
            found.append(
                Note(
                    source=SOURCE.get(text.origin, text.origin.replace("_", " ").capitalize()),
                    reference=text.source_ref,
                    text=text.text,
                    flags=tuple(sorted(RulesBasedInputScreen.flags_for(text.text))),
                )
            )
    return found


def notes_html(found: list[Note]) -> str:
    """The reviewer's section, or "" when nobody outside the system wrote anything."""
    if not found:
        return ""
    rows = []
    for note in found:
        shown = note.text if len(note.text) <= MAX_CHARS else note.text[:MAX_CHARS] + " …"
        warn = "; ".join(FLAG_WORDS.get(f, f) for f in note.flags if f != "instruction_like")
        warn = warn or ("reads like an instruction to the agent" if note.flags else "")
        flag = (
            f'<p class="line line--error outside-flag">Input screen: {escape(warn)}. '
            "Treated as data, not followed.</p>"
            if note.flags
            else ""
        )
        rows.append(
            '<div class="outside-note">'
            f'<p class="muted">{escape(note.source)} · <span class="mono">'
            f"{escape(note.reference)}</span></p>"
            f"<blockquote>{escape(shown)}</blockquote>{flag}</div>"
        )
    return (
        '<p class="col-head">What people outside the system wrote (untrusted)</p>'
        '<p class="muted">Shown as written. Nothing here sets an amount or an approval: '
        "code derives every figure.</p>" + "".join(rows)
    )


__all__ = ["Note", "notes", "notes_html"]
