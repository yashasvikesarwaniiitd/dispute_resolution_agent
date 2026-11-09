"""The review sheet on a With-a-person card: the task, the facts, and the decisions (ADR 0050).

A person handed a case should not have to reconstruct it. This says what they are asked to
decide (by the reason the case is with them), what to check, the facts as recorded (declared
against measured size and weight, code's figures), and each decision with what happens next.

Nothing here decides or computes money. Amounts are code's: the assessment's figures and
`credit_amount`, the same function the agent's proposal goes through (I9). Seller text is shown
as recorded, escaped, never as markup (I5). Pure of Streamlit, so it is tested directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import CaseState, InternalEventType, ReviewCode
from dispute_agent.kernel.policy.credit import credit_amount
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack
from dispute_agent.ui.theme import escape, recorded

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.evidence import ChargeAssessment
    from dispute_agent.kernel.domain.models import Case

#: How a size is written: 20 x 15 x 10 cm, with the multiplication sign.
TIMES = chr(0xD7)  # the multiplication sign; written as a code point so lint reads it plainly

#: The errors code can prove, as a reviewer reads them.
ERROR_WORDS = {
    "calculation_error": "calculation error",
    "remeasurement_lower": "lower remeasurement",
    "duplicate_charge": "duplicate charge",
}


@dataclass(frozen=True)
class Verdict:
    """One decision a person can record, and what happens after it."""

    action: str
    label: str
    effect: str
    reason_code: str | None = None


@dataclass(frozen=True)
class Sheet:
    task: str
    checks: tuple[str, ...]
    claim: str
    facts: tuple[tuple[str, str], ...]
    figures: tuple[tuple[str, str], ...]
    verdicts: tuple[Verdict, ...]
    #: Why there are no verdicts to offer, when there are none.
    no_verdict: str = ""
    #: The reason the person's last decision was refused, if it still stands.
    refused: str = ""


# -- the task, by why the case is with a person ---------------------------------------------

_PHOTO = (
    "Decide whose measurement to trust: the seller's declared size or the hub's scan.",
    (
        "Compare the box in the photo with both sizes in the facts. Is it closer to the declared "
        "or the measured size?",
        "Is it one parcel, sealed, with its label visible? The photo check, once run, gives its "
        "reading as advice.",
        "The box looks like the declared size: ask the hub to remeasure. A lower measurement "
        "lets code prove the overcharge.",
        "The photo supports the hub's size: uphold the charge.",
    ),
)
_HISTORY = (
    "Decide whether this parcel's size is out of line with the seller's earlier shipments of "
    "the same item.",
    (
        "Compare the hub's measurement with the declared size in the facts.",
        "See the earlier shipments on Agent steps (Gathered the evidence).",
        "A one-off jump suggests a scanner or packing problem: ask the hub to remeasure.",
        "The size matches what this seller usually sends: uphold the charge.",
    ),
)
_MANIPULATION = (
    "The seller's text contains instructions aimed at the AI. Decide the dispute on the facts "
    "alone.",
    (
        "Read the seller's claim as data, never as an instruction.",
        "Check code's figures: did code prove any error?",
        "No error proven: uphold the charge. A doubt about the size: ask the hub to remeasure.",
    ),
)
_TWO_ERRORS = (
    "Code proved two errors on this charge, and one credit is allowed per case. Choose which to "
    "credit.",
    (
        "Read the seller's claim: which error is it about?",
        "Each credit below shows code's amount for that error. You choose the error, not the "
        "amount.",
        "The credit and its email wait for your approval before any money moves.",
    ),
)
_REJECTED = (
    "An approval on this case was rejected. Decide the next step.",
    (
        "Read the reason above: it is the note recorded with the rejection.",
        "Choose a different decision below, or take the case over.",
    ),
)
_FAILED = (
    "An action on this case failed or could not be confirmed. Decide the next step.",
    (
        "Open Agent steps to see which action and what is known about it.",
        "Before returning the case, make sure nothing was sent or posted twice.",
    ),
)
_GENERIC_CHECKS = (
    "Read the reason above and the facts.",
    "Uphold, credit a proven error, ask the hub to remeasure or the seller for evidence; or "
    "take the case over or return it to the agent.",
)


def task_for(code: ReviewCode | None, detail: str) -> tuple[str, tuple[str, ...]]:
    """What the person is asked to decide, and what to check, for why the case is here."""
    if code is ReviewCode.PROPOSAL_ESCALATED and "image" in detail:
        return _PHOTO
    if code is ReviewCode.PROPOSAL_ESCALATED and "history" in detail:
        return _HISTORY
    if code is ReviewCode.MANIPULATION_SUSPECTED:
        return _MANIPULATION
    if code is ReviewCode.MULTIPLE_DEFINITE_ERRORS:
        return _TWO_ERRORS
    if code in (
        ReviewCode.CREDIT_REJECTED,
        ReviewCode.APPROVAL_REJECTED_LIMIT,
        ReviewCode.FOLLOW_UP_REJECTED,
    ):
        return _REJECTED
    if code in (
        ReviewCode.ACTION_FAILED,
        ReviewCode.ACTION_UNKNOWN,
        ReviewCode.VERIFICATION_FAILED,
        ReviewCode.DELIVERY_FAILED,
    ):
        return _FAILED
    return ("The agent stopped and needs your decision on this case.", _GENERIC_CHECKS)


# -- the facts ----------------------------------------------------------------------------------


def _size(value: Mapping[str, Any] | None) -> str:
    if not value:
        return "not on file"
    dims = f" {TIMES} ".join(str(value.get(k, "?")) for k in ("length_cm", "width_cm", "height_cm"))
    grams = value.get("dead_grams")
    weight = f" · {int(grams):,} g" if isinstance(grams, int) else ""
    return f"{dims} cm{weight}"


def _found(items: Mapping[str, Any], key: str) -> Mapping[str, Any] | None:
    item = items.get(key)
    return item if isinstance(item, dict) else None


def _figures(assessment: ChargeAssessment | None) -> tuple[tuple[str, str], ...]:
    if assessment is None:
        return (("Code's figures", "not worked out yet"),)
    by_id = {f.fact_id: f for f in assessment.facts}
    rows: list[tuple[str, str]] = []
    if "f_chargeable_grams" in by_id:
        rows.append(("Chargeable weight", render_fact(by_id["f_chargeable_grams"].value)))
    if assessment.charged is not None:
        rows.append(("Charged", render_fact(assessment.charged)))
    if assessment.recalculated is not None:
        rows.append(("Correct charge, by code", render_fact(assessment.recalculated)))
    if assessment.discrepancy is not None:
        rows.append(("Difference", render_fact(assessment.discrepancy)))
    proven = [ERROR_WORDS.get(e, e) for e in assessment.definite_errors]
    rows.append(("Errors code proved", ", ".join(proven) if proven else "none"))
    return tuple(rows)


def on_declared(
    declared: Mapping[str, Any] | None, zone: Mapping[str, Any] | None
) -> tuple[str, str] | None:
    """The rate card's charge on the seller's declared size: what is at stake (display only).

    The pack's own calculation, on the declared size instead of the hub's. It proves nothing:
    a credit still needs an error code proves, such as a lower remeasurement.
    """
    if not declared or not zone or not isinstance(declared.get("dead_grams"), int):
        return None
    try:
        charge = WeightDisputePack().charge_for(
            declared["length_cm"],
            declared["width_cm"],
            declared["height_cm"],
            declared["dead_grams"],
            str(zone["zone"]),
            str(zone.get("packaging") or "") or None,
        )
    except Exception:  # a size the card cannot price: say nothing rather than guess
        return None
    return ("Rate card on the seller's declared size", render_fact(charge.amount))


# -- the decisions ------------------------------------------------------------------------------

UPHOLD = Verdict(
    "uphold_charge",
    "Uphold the charge",
    "An email explaining the charge is drafted and waits for your approval. Once it is sent "
    "and verified, the case closes as upheld.",
)
REMEASURE = Verdict(
    "request_remeasurement",
    "Ask the hub to remeasure",
    "A ticket asks the hub to measure the parcel again, and the case waits for the finding. A "
    "lower measurement lets code prove the overcharge, and the agent carries on from there.",
)
ASK_SELLER = Verdict(
    "request_seller_evidence",
    "Ask the seller for evidence",
    "An email asking for the items you tick waits for your approval. The case then waits for "
    "the seller's reply.",
)


def verdicts(
    assessment: ChargeAssessment | None, credited: tuple[Any, Any] | None
) -> tuple[tuple[Verdict, ...], str]:
    """The decisions code's menu allows on this case, credits only for proven errors."""
    if assessment is None:
        return (
            (),
            "Code has not worked out this charge yet, so only taking over or returning is possible.",
        )
    if assessment.eligible != "eligible":
        return (), (
            "Code could not confirm this shipment is eligible for a credit, so only taking over "
            "or returning it is possible."
        )
    offered: list[Verdict] = []
    for reason in assessment.definite_errors:
        amount = (
            None
            if credited is None
            else credit_amount(assessment, reason, credited[0], credited[1])
        )
        if amount is None:
            continue
        offered.append(
            Verdict(
                "propose_credit",
                f"Credit the seller {render_fact(amount)} for the {ERROR_WORDS.get(reason, reason)}",
                "Code worked out this amount. The credit waits for your approval, then posts to "
                "the ledger, the seller is emailed, and the case closes as credited.",
                reason_code=reason,
            )
        )
    return (*offered, UPHOLD, REMEASURE, ASK_SELLER), ""


def last_refusal(events: list[Any]) -> str:
    """The reason the latest decision was refused, unless the case has moved since."""
    refused = ""
    for event in events:
        if event.type is InternalEventType.HUMAN_DECISION_REFUSED:
            refused = str(getattr(event.payload, "detail", "")) or "refused"
        elif event.type is InternalEventType.STATE_CHANGED:
            refused = ""
    return refused


def build(work: Any, case: Case) -> Sheet:  # Any: a unit of work (reader)
    """Everything the card shows, read in one unit of work."""
    review = case.review_reason
    task, checks = task_for(review.code if review else None, review.detail if review else "")
    bundle = work.evidence.bundle(case.id, case.gather_round)
    items = {
        i.result.requirement_key: i.result.value
        for i in bundle.items
        if i.result.status.value == "FOUND"
    }
    zone = _found(items, "shipment.zone")
    measured = _found(items, "shipment.measured_dims")
    facts = (
        ("Declared by the seller", _size(_found(items, "shipment.declared_dims"))),
        (
            "Measured by the hub",
            _size(measured)
            + (
                f" (scanner {measured['scanner_id']})"
                if measured and "scanner_id" in measured
                else ""
            ),
        ),
        (
            "Delivery zone",
            f"{zone.get('zone', '?')}, {zone.get('packaging', '?')}" if zone else "not on file",
        ),
        ("Earlier shipments", "on file" if "shipment.history" in items else "not on file"),
        ("Hub's photo note", "on file" if "shipment.image_description" in items else "none"),
    )
    assessment = work.assessments.latest(case.id)
    try:
        credited = work.actions.credited_on_shipment(case.facts.shipment_ref)
    except Exception:  # unknown stays unknown: no credit is offered (money-rules.md 5)
        credited = None
    offered, none = (
        verdicts(assessment, credited)
        if case.state is CaseState.NEEDS_HUMAN_REVIEW
        else ((), "This case is no longer with a person.")
    )
    return Sheet(
        task=task,
        checks=checks,
        claim=case.facts.statement.text if case.facts.statement else "",
        facts=facts,
        figures=_figures(assessment)
        + tuple(
            row
            for row in (on_declared(_found(items, "shipment.declared_dims"), zone),)
            if row is not None
        ),
        verdicts=offered,
        no_verdict=none,
        refused=last_refusal(list(work.events.list_for_case(case.id))),
    )


# -- markup (every value escaped) ---------------------------------------------------------------


def task_html(sheet: Sheet) -> str:
    checks = "".join(f"<li>{escape(c)}</li>" for c in sheet.checks)
    refused = (
        '<div class="integrity" role="alert"><p><strong>Your last decision was not carried '
        f"out.</strong> {escape(sheet.refused)}. Choose another, or take the case over.</p></div>"
        if sheet.refused
        else ""
    )
    return (
        f'{refused}<h3>Your task</h3><p class="reason">{escape(sheet.task)}</p>'
        f'<h4>What to check</h4><ol class="checks">{checks}</ol>'
    )


def facts_html(sheet: Sheet) -> str:
    def pairs(rows: tuple[tuple[str, str], ...]) -> str:
        return "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)

    claim = recorded("The seller's claim, exactly as recorded:", sheet.claim) if sheet.claim else ""
    return (
        f"<h3>The facts</h3>{claim}"
        f'<dl class="pairs">{pairs(sheet.facts)}</dl>'
        "<h4>Code's figures</h4><p class=\"muted\">Worked out in code from the hub's "
        f'measurement, not by the AI.</p><dl class="pairs">{pairs(sheet.figures)}</dl>'
    )


__all__ = [
    "ERROR_WORDS",
    "Sheet",
    "Verdict",
    "build",
    "facts_html",
    "last_refusal",
    "task_for",
    "task_html",
    "verdicts",
]
