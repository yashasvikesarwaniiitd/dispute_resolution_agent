"""The Dispute Desk workspace's read model (design handoff v2, "Dispute Workspace v2").

Each function turns what the Streamlit console already reads - journey, trail, case sheet,
review queue, outside notes, seller catalogue, ledger rows - into plain JSON for the web app.
Nothing here decides or computes money: every figure is code's, rendered by the same functions
the console uses, so the two faces of the system can never disagree.

Outside text (seller and hub words) is returned as plain strings with its source; the web app
renders it as text, never as markup (I5).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole
from dispute_agent.ui import case_sheet, data, photo_check
from dispute_agent.ui import review_view as words
from dispute_agent.ui.came_back import came_back
from dispute_agent.ui.history_panel import _sides, comparison_for, headline, owner_name
from dispute_agent.ui.journey import WORKING, journey
from dispute_agent.ui.labels import EVIDENCE, handed_over, local, reason, state
from dispute_agent.ui.outside_notes import FLAG_WORDS, notes
from dispute_agent.ui.photos import photo_for
from dispute_agent.ui.review import Card, credit_basis, unknown_actions
from dispute_agent.ui.seller import Shipment, personas, seller_shipments, ticket
from dispute_agent.ui.trail import build, recommendation

if TYPE_CHECKING:
    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.models import Case

#: The views rail (design 2.2): (section, key, label). "My work" means a person is the next
#: step; "Agent has it" means no one needs to look.
VIEWS: tuple[tuple[str, str, str], ...] = (
    ("My work", "needs", "All needing me"),
    ("My work", "credits", "Proven credits"),
    ("My work", "emails", "Emails to approve"),
    ("My work", "handed", "Handed to me"),
    ("My work", "replied", "Seller replied"),
    ("My work", "unverified", "Could not verify"),
    ("My work", "done", "Done today"),
    ("Agent has it", "hub", "Waiting on hub"),
    ("Agent has it", "investigating", "Investigating"),
    ("Agent has it", "sellerwait", "Waiting on seller"),
    ("History", "closed", "All closed"),
)
NEEDS = frozenset({"credits", "emails", "handed", "replied", "unverified"})

#: A reader that only views: ReviewConsole needs a person, and viewing records nothing.
VIEWER = Actor(kind=ActorKind.HUMAN, id="human:viewer")


#: The console's shared wording names Streamlit screens; in the workspace the same place is
#: this case view. Code's own text, never outside text (I5).
PLACES = (
    ("on the Review screen", "here"),
    ("the Review screen", "this case"),
    ("on Agent steps", "in the Full record tab"),
    ("Open Agent steps", "Open the Full record tab"),
    ("Agent steps", "the Full record tab"),
)


def here(text: str) -> str:
    for old, new in PLACES:
        text = text.replace(old, new)
    return text


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.isoformat()


def pending_approvals(work: SqliteUnitOfWork, case: Case) -> list[Approval]:
    return [a for a in work.approvals.for_case(case.id) if a.decision is ApprovalDecision.PENDING]


def views_for(case: Case, pending: list[Approval], unverified: bool, today: str) -> list[str]:
    """Which rail views a case belongs to. A case can sit in several (credits and needs)."""
    found: list[str] = []
    if any(a.request.subject is ApprovalSubject.CREDIT for a in pending):
        found.append("credits")
    if any(a.request.subject is ApprovalSubject.MESSAGE for a in pending):
        found.append("emails")
    if case.state is CaseState.NEEDS_HUMAN_REVIEW:
        found.append("handed")
    if unverified:
        found.append("unverified")
    closed = case.closed_at is not None
    if case.reopen_count > 0 and not closed and not found:
        found.append("replied")
    if any(v in NEEDS for v in found):
        found.insert(0, "needs")
    elif closed:
        found.append("closed")
        if local(case.closed_at).date().isoformat() == today:  # type: ignore[arg-type]
            found.append("done")
    elif case.state is CaseState.AWAITING_OPS:
        found.append("hub")
    elif case.state is CaseState.AWAITING_SELLER:
        found.append("sellerwait")
    elif case.state in WORKING or case.state is CaseState.AWAITING_APPROVAL:
        found.append("investigating")
    return found


def tag(case: Case, pending: list[Approval], unverified: bool) -> tuple[str, str]:
    """The list row's chip (design 2.2 tag chips): what the person is asked to do, in words."""
    if unverified:
        return "Could not verify", "failed"
    if any(a.request.subject is ApprovalSubject.CREDIT for a in pending):
        return "Approve credit", "proven"
    if pending:
        return "Approve email", "neutral"
    if case.state is CaseState.NEEDS_HUMAN_REVIEW:
        return "Handed to you", "you"
    if case.closed_at is not None:
        label, _ = state(case.state)
        return label, "proven" if case.state is CaseState.RESOLVED_CREDITED else "neutral"
    if case.reopen_count > 0:
        return "Reopened · agent", "agent"
    if case.state is CaseState.AWAITING_OPS:
        return "Waiting on hub", "hub"
    if case.state is CaseState.AWAITING_SELLER:
        return "Waiting on seller", "hub"
    return "Agent investigating", "agent"


def kind(case: Case, pending: list[Approval], unverified: bool) -> str:
    """Which case view to draw (design 3.4): the layout follows the work."""
    if any(a.request.subject is ApprovalSubject.CREDIT for a in pending):
        return "credit"
    if pending:
        return "email"
    if case.state is CaseState.NEEDS_HUMAN_REVIEW:
        return "handover"
    if unverified:
        return "unverified"
    if case.closed_at is not None:
        return "done"
    if case.reopen_count > 0:
        return "reopened"
    if case.state in (CaseState.AWAITING_OPS, CaseState.AWAITING_SELLER):
        return "waiting"
    return "investigating"


def due(case: Case, pending: list[Approval], now: datetime) -> dict[str, Any]:
    """The time line on a row: an approval's expiry, else how long the case has waited."""
    if pending:
        expires = min(a.request.expires_at for a in pending)
        hours = (expires - now).total_seconds() / 3600
        return {
            "text": f"expires in {max(0, round(hours))}h" if hours >= 1 else "expires soon",
            "at_risk": hours < 24,
            "at": iso(expires),
        }
    waited = (now - case.state_entered_at).total_seconds() / 3600
    text = f"{round(waited)}h" if waited >= 1 else f"{max(1, round(waited * 60))}m"
    return {"text": text, "at_risk": False, "at": iso(case.state_entered_at)}


def work_item(work: SqliteUnitOfWork, case: Case, now: datetime) -> dict[str, Any]:
    pending = pending_approvals(work, case)
    unverified = case.closed_at is None and bool(unknown_actions(work, case.id))
    where = journey(work, case)
    label, tone = state(case.state)
    chip, chip_tone = tag(case, pending, unverified)
    amounts = [
        render_fact(a.request.amount)
        for a in pending
        if a.request.subject is ApprovalSubject.CREDIT and a.request.amount is not None
    ]
    return {
        "case_id": str(case.id),
        "shipment_ref": case.facts.shipment_ref,
        "seller_ref": case.facts.seller_ref,
        "seller": owner_name(case.facts.shipment_ref) or case.facts.seller_ref,
        "state": case.state.value,
        "status": label,
        "tone": tone.value,
        "summary": here(where.headline),
        "tag": chip,
        "tag_tone": chip_tone,
        "kind": kind(case, pending, unverified),
        "views": views_for(case, pending, unverified, local(now).date().isoformat()),
        "credit": amounts[0] if amounts else None,
        "due": due(case, pending, now),
        "opened_at": iso(case.opened_at),
        "updated_at": iso(case.updated_at),
        "reopen_count": case.reopen_count,
        "is_open": case.closed_at is None,
    }


def work_list(work: SqliteUnitOfWork, now: datetime) -> dict[str, Any]:
    cases = work.cases.list_in_states(list(CaseState))
    items = [work_item(work, c, now) for c in cases]
    # Most urgent first: approvals by expiry, then the oldest waits.
    items.sort(key=lambda i: (not i["due"]["at_risk"], i["due"]["at"] or ""))
    counts = {key: sum(1 for i in items if key in i["views"]) for _, key, _ in VIEWS}
    return {
        "views": [
            {"section": section, "key": key, "label": label, "count": counts[key]}
            for section, key, label in VIEWS
        ],
        "items": items,
    }


# -- one case ---------------------------------------------------------------------------------


def _dims(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not all(
        k in value for k in ("length_cm", "width_cm", "height_cm")
    ):
        return None
    return {
        "length_cm": float(value["length_cm"]),
        "width_cm": float(value["width_cm"]),
        "height_cm": float(value["height_cm"]),
        "grams": value.get("dead_grams"),
    }


def _approval(work: SqliteUnitOfWork, case: Case, approval: Approval, rec: Any) -> dict[str, Any]:
    console = ReviewConsole(store=work.approvals, clock=_Clock(), reviewer=VIEWER)
    item = console.view(
        approval, intents=work.intents, assessments=work.assessments, actions=work.actions
    )
    request = approval.request
    out: dict[str, Any] = {
        "approval_id": str(approval.id),
        "title": words.kind_title(item),
        "what": words.what(item),
        "approve_label": words.approve_label(item),
        "is_credit": item.is_credit,
        "requested_at": iso(request.requested_at),
        "expires_at": iso(request.expires_at),
        "matches_approval": item.matches_approval,
        "numbers": [{"value": v, "source": s} for v, s in item.numbers],
    }
    if item.is_credit:
        duplicate, already, remeasured, terms = credit_basis(
            work, Card(item=item, case=ticket(case), recommendation=rec)
        )
        out |= {
            "amount": item.credit_amount,
            "reason": item.credit_reason,
            "reason_words": (item.credit_reason or "").replace("_", " "),
            "equation": [{"label": k, "value": v} for k, v in terms] if terms else None,
            "already_credited": already,
            "duplicate_debit": duplicate,
            "remeasured_charge": remeasured,
            "figures": [{"label": k, "value": v} for k, v in item.evidence],
        }
    else:
        out |= {"to": item.to, "subject": item.subject, "body": item.body}
    return out


class _Clock:
    """ReviewConsole's clock for reads: viewing never records a time."""

    def now(self) -> datetime:
        from datetime import UTC

        return datetime.now(UTC)


def _recommendation(rec: Any) -> dict[str, Any] | None:
    if rec is None:
        return None
    return {
        "action": rec.action,
        "confidence": rec.confidence,
        "by": rec.by,
        "rationale": rec.rationale,
        "findings": [
            {"kind": kind_, "statement": statement, "cites": list(cites)}
            for kind_, statement, cites in rec.findings
        ],
    }


def _steps(work: SqliteUnitOfWork, case: Case) -> dict[str, Any]:
    trail = build(work, case, usage=data.ai_usage(case.id), policy=data.policy())
    return {
        "ai_calls": trail.ai_calls,
        "ai_cost_usd": str(trail.ai_cost),
        "round_titles": {str(k): v for k, v in trail.round_titles.items()},
        "steps": [
            {
                "at": iso(s.at),
                "actor": s.actor.value,
                "title": here(s.title),
                "summary": here(s.summary),
                "lines": list(s.lines),
                "quoted": list(s.quoted),
                "quote_caption": s.quote_caption,
                "proposer": s.proposer,
                "round": s.round,
                "minor": s.minor,
                "mark": s.mark.value,
                "tag": s.tag,
            }
            for s in trail.steps
        ],
    }


def _handover(work: SqliteUnitOfWork, case: Case) -> dict[str, Any]:
    """The decision menu a person may choose from: code's verdicts plus the desk's own."""
    from dispute_agent.ui.desk import can_confirm_withdrawal

    sheet = case_sheet.build(work, case)
    paid = work.actions.has_verified_credit(case.id)
    review = case.review_reason
    why = handed_over(review.code, review.detail) if review else ("", "")
    return {
        "reason": reason(review.code) if review else "With a person",
        "reason_detail": why[0],
        "rule": why[1],
        "task": here(sheet.task),
        "checks": [here(c) for c in sheet.checks],
        "refused": sheet.refused,
        "no_verdict": sheet.no_verdict,
        "verdicts": [
            {
                "action": v.action,
                "label": v.label,
                "effect": v.effect,
                "reason_code": v.reason_code,
            }
            for v in sheet.verdicts
        ],
        "can_return": not paid,
        "credit_verified": paid,
        "can_withdraw": can_confirm_withdrawal(case),
        "unknown": [
            {"action_id": str(u.action_id), "what": u.what, "where": u.where}
            for u in unknown_actions(work, case.id)
        ],
    }


def _evidence(work: SqliteUnitOfWork, case: Case) -> dict[str, Any]:
    bundle = work.evidence.bundle(case.id, case.gather_round)
    found = {
        i.result.requirement_key: i.result.value
        for i in bundle.items
        if i.result.status.value == "FOUND"
    }
    finding = found.get("ops.finding")
    remeasured = (
        _dims(finding)
        if isinstance(finding, dict) and finding.get("kind") == "remeasured"
        else None
    )
    comparison = comparison_for(case.facts.shipment_ref)
    checked = photo_check.latest(bundle.items)
    measured = found.get("shipment.measured_dims")
    return {
        "declared": _dims(found.get("shipment.declared_dims")),
        "measured": _dims(measured),
        "scanner": measured.get("scanner_id") if isinstance(measured, dict) else None,
        "remeasured": remeasured,
        "items": [
            {
                "key": i.result.requirement_key,
                "name": EVIDENCE.get(i.result.requirement_key, i.result.requirement_key),
                "status": i.result.status.value,
            }
            for i in bundle.items
        ],
        "history": None
        if comparison is None
        else {
            "headline": headline(comparison),
            "verdict": comparison.get("verdict"),
            "this_parcel": _sides(comparison.get("current")),
            "usual": _sides(comparison.get("typical")) if comparison.get("typical") else None,
            "usual_volume_cm3": (comparison.get("range") or {}).get("volume_cm3"),
            "references": comparison.get("references", 0),
            "reference_refs": list(comparison.get("reference_refs") or []),
            "outlier_refs": list(comparison.get("outlier_refs") or []),
        },
        "photo": photo_for(case.facts.shipment_ref) is not None,
        "photo_check": None
        if checked is None
        else {
            "ok": checked.ok,
            "summary": checked.summary,
            "rows": [{"label": k, "value": v} for k, v in checked.rows],
            "observations": list(checked.observations),
            "by": checked.by,
        },
    }


def shipment_view(s: Shipment) -> dict[str, Any]:
    return {
        "ref": s.ref,
        "claim_ref": s.claim_ref,
        "seller_ref": s.seller_ref,
        "status": s.status,
        "zone": s.zone,
        "declared": s.declared,
        "measured": s.measured,
        "charged": s.charged,
        "expected": s.on_declared,
        "over_card": s.over_card,
        "billing_error": s.billing_error,
        "verdict": s.verdict,
        "breakdown": [{"step": step, "declared": d, "measured": m} for step, d, m in s.breakdown],
        "disputed": s.disputed,
    }


def shipment_by_ref(ref: str) -> Shipment | None:
    for persona in personas():
        for s in seller_shipments(persona["seller_id"]):
            if s.ref == ref:
                return s
    from dispute_agent.ui.seller import catalogue

    return next((s for s in catalogue(data.SHIPMENTS) if s.ref == ref), None)


def persona_for(seller_ref: str) -> dict[str, Any] | None:
    return next((p for p in personas() if p["seller_id"] == seller_ref), None)


def case_detail(work: SqliteUnitOfWork, case: Case, now: datetime) -> dict[str, Any]:
    pending = pending_approvals(work, case)
    unverified = case.closed_at is None and bool(unknown_actions(work, case.id))
    proposals = [p for p in work.proposals.for_case(case.id) if p.reasoner != "human"]
    rec = recommendation(proposals[-1]) if proposals else None
    where = journey(work, case)
    label, tone = state(case.state)
    chip, chip_tone = tag(case, pending, unverified)
    shipment = shipment_by_ref(case.facts.shipment_ref)
    others = [
        work_item(work, c, now)
        for c in work.cases.list_in_states(list(CaseState))
        if c.facts.seller_ref == case.facts.seller_ref and c.id != case.id and c.closed_at is None
    ]
    return {
        "case_id": str(case.id),
        "shipment_ref": case.facts.shipment_ref,
        "claim_ref": case.facts.claim_ref,
        "seller_ref": case.facts.seller_ref,
        "seller": owner_name(case.facts.shipment_ref) or case.facts.seller_ref,
        "state": case.state.value,
        "status": label,
        "tone": tone.value,
        "tag": chip,
        "tag_tone": chip_tone,
        "kind": kind(case, pending, unverified),
        "is_open": case.closed_at is None,
        "opened_at": iso(case.opened_at),
        "updated_at": iso(case.updated_at),
        "closed_at": iso(case.closed_at),
        "reopen_count": case.reopen_count,
        "rejection_count": case.rejection_count,
        "round": case.gather_round,
        "due": due(case, pending, now),
        "journey": {
            "headline": here(where.headline),
            "next_step": here(where.next_step),
            "why": here(where.why),
            "why_code": where.why_code,
            "phases": [{"label": p, "mark": m.value} for p, m in where.phases],
            "figures": [{"label": k, "value": v} for k, v in where.figures],
            "lead": where.lead,
            "figure_note": where.figure_note,
        },
        "recommendation": _recommendation(rec),
        "approvals": [_approval(work, case, a, rec) for a in pending],
        "handover": _handover(work, case) if case.state is CaseState.NEEDS_HUMAN_REVIEW else None,
        "came_back": [
            {"who": c.who, "at": iso(c.at), "summary": c.summary, "words": list(c.words)}
            for c in came_back(work, case)
        ],
        "outside": [
            {
                "source": n.source,
                "reference": n.reference,
                "text": n.text,
                "flags": [FLAG_WORDS.get(f, f) for f in n.flags],
            }
            for n in notes(work, str(case.id))
        ],
        "statement": case.facts.statement.text if case.facts.statement else "",
        "evidence": _evidence(work, case),
        "record": _steps(work, case),
        "shipment": None if shipment is None else shipment_view(shipment),
        "persona": persona_for(case.facts.seller_ref),
        "other_open": others,
    }


__all__ = ["NEEDS", "VIEWS", "case_detail", "shipment_by_ref", "shipment_view", "work_list"]
