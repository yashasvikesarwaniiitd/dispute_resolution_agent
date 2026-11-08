"""Where a case is, and what happens next, in one sentence each (ADR 0044, Agent steps).

The top of the Agent steps screen: a headline, why the case is there, the next step, and
five phases - raised, investigated, approved, acted, closed - each marked done, in progress,
waiting, with a person, not needed (a later phase happened without it) or not reached (the
case closed first). Every sentence is chosen from the case's state and its records (pending
approval, verified credit, the hub's ticket, the review reason, the assessment's eligibility);
nothing is inferred and no figure is computed here - amounts come from the records, as
stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
)
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.ui.labels import Tone, handed_over, state, words

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.models import Case

PHASES = ("Raised", "Investigated", "Approved", "Acted", "Closed")
#: Which of code's facts the headline card shows, and how it names them.
FIGURES = (
    ("f_charged", "Charged"),
    ("f_recalculated", "Correct charge"),
    ("f_discrepancy", "Overcharged"),
)


class Mark(StrEnum):
    DONE = "done"
    CURRENT = "current"
    WAITING = "waiting"
    PERSON = "person"
    #: Closed case: a later phase happened without this one (no approval was needed).
    SKIPPED = "skipped"
    #: Closed case: it ended before this phase.
    NOT_REACHED = "not_reached"
    TODO = "todo"


@dataclass(frozen=True)
class Journey:
    headline: str
    next_step: str
    tone: Tone
    phases: tuple[tuple[str, Mark], ...]
    #: Code's latest figures for the case - charged, correct, overcharged - as rendered facts;
    #: empty until code has recalculated, and for a shipment proved ineligible (its figures
    #: answer a question that was never asked). Never computed here.
    figures: tuple[tuple[str, str], ...] = ()
    #: Why the case is where it is, when the state alone does not say: the review reason, or
    #: which check found the shipment ineligible.
    why: str = ""
    #: The rule code behind `why` ("B5", "R21"), shown small after it; empty when none.
    why_code: str = ""
    #: The label of the figure the case turns on (the overcharge, a duplicate debit).
    lead: str = ""
    #: A sentence under the figures when they could be misread (a duplicate charge).
    figure_note: str = ""


WORKING = frozenset(
    {
        CaseState.RECEIVED,
        CaseState.GATHERING_EVIDENCE,
        CaseState.CALCULATING,
        CaseState.INVESTIGATING,
        CaseState.EXECUTING,
        CaseState.VERIFYING,
    }
)


def journey(work: SqliteUnitOfWork, case: Case) -> Journey:
    approvals = list(work.approvals.for_case(case.id))
    actions = list(work.actions.for_case(case.id))
    pending = [a for a in approvals if a.decision is ApprovalDecision.PENDING]
    credited = [
        a
        for a in approvals
        if a.request.subject is ApprovalSubject.CREDIT and a.decision is ApprovalDecision.APPROVED
    ]
    issues = [a.external_ref for a in actions if a.action_type is ActionType.UPSERT_ISSUE]
    latest = work.assessments.latest(case.id)
    proved_ineligible = latest is not None and latest.eligible == "ineligible"
    headline, why, next_step = _words(case, pending, credited, issues, proved_ineligible)
    why_code = ""
    if case.state is CaseState.NEEDS_HUMAN_REVIEW and case.review_reason is not None:
        why, why_code = handed_over(case.review_reason.code, case.review_reason.detail)
    facts = {f.fact_id: render_fact(f.value) for f in (latest.facts if latest else ())}
    figures = (
        ()
        if proved_ineligible
        else tuple((label, facts[key]) for key, label in FIGURES if key in facts)
    )
    lead, note = ("Overcharged" if latest and latest.definite_errors else ""), ""
    duplicate = latest.duplicate_debit_total if latest is not None else None
    if figures and latest and "duplicate_charge" in latest.definite_errors and duplicate:
        # The weight charge was right; the credit's basis is the second debit (HANDOFF 4.2).
        lead = "Duplicate debit (the credit's basis)"
        figures = (
            (lead, render_fact(duplicate)),
            ("Weight charge", facts.get("f_charged", "")),
            ("Correct weight charge", facts.get("f_recalculated", "")),
        )
        note = (
            "The weight charge itself was right. The seller was billed for it twice, so the "
            "credit returns the second debit."
        )
    return Journey(
        headline=headline,
        next_step=next_step,
        tone=state(case.state)[1],
        why=why,
        why_code=why_code,
        lead=lead,
        figure_note=note,
        phases=_phases(
            case,
            # Looking at the evidence is investigating, whether or not a proposal followed.
            investigated=latest is not None or bool(list(work.proposals.for_case(case.id))),
            approved=any(a.decision is ApprovalDecision.APPROVED for a in approvals)
            or bool(actions),
            acted=any(a.status is ActionStatus.VERIFIED for a in actions),
        ),
        figures=figures,
    )


def _words(
    case: Case,
    pending: Sequence[Approval],
    credited: Sequence[Approval],
    issues: Sequence[str | None],
    proved_ineligible: bool,
) -> tuple[str, str, str]:
    """The headline, why (when the state does not say it alone), and what happens next."""
    s = case.state
    if s is CaseState.RESOLVED_CREDITED:
        amount = credited[-1].request.amount if credited else None
        return (
            f"Resolved: the seller was credited {render_fact(amount)}."
            if amount
            else "Resolved: the seller was credited.",
            "Code proved an overcharge, and a reviewer approved the credit.",
            "Nothing left to do. The credit was proved by reading it back from the ledger.",
        )
    if s is CaseState.RESOLVED_UPHELD:
        return (
            "Resolved: the charge was correct, and the seller was told why.",
            "",
            "Nothing left to do. The seller can reply with new evidence within 7 days.",
        )
    if s is CaseState.REJECTED_INELIGIBLE:
        found = (
            "The scan history shows the shipment was not delivered"
            if proved_ineligible
            else "The shipment's status shows it was not delivered"
        )
        return (
            "Closed: this shipment could not be disputed.",
            f"{found}. Only delivered shipments can be disputed.",
            "Nothing left to do.",
        )
    if s is CaseState.WITHDRAWN:
        return "Closed: the seller withdrew the dispute.", "", "Nothing left to do."
    if s is CaseState.CLOSED_NO_RESPONSE:
        return (
            "Closed: the seller did not reply.",
            "",
            "If they reply later, the case opens again and is looked at fresh.",
        )
    if s is CaseState.HANDED_TO_HUMAN:
        return (
            "A person took this case over. The agent has stopped.",
            "",
            "The person who took it handles it from here.",
        )
    if s is CaseState.AWAITING_APPROVAL:
        what = "a decision"
        if pending:
            request = pending[-1].request
            if request.subject is ApprovalSubject.CREDIT:
                what = f"a {render_fact(request.amount)} credit" if request.amount else "a credit"
            else:
                kind = request.message_kind
                what = f"an email to the seller ({words(kind.value)})" if kind else "an email"
        return (
            f"Waiting for a reviewer to approve {what}.",
            "Policy allows it, but a person must approve first.",
            "Approve or reject it on the Review screen. Nothing is sent until then.",
        )
    if s is CaseState.NEEDS_HUMAN_REVIEW:
        reason = case.review_reason
        why = words(reason.code.value).capitalize() if reason else "A reason the agent recorded"
        detail = f". {reason.detail}" if reason and reason.detail else ""
        return (
            "The agent stopped and handed this case to a person.",
            f"{why}{detail}",
            "A reviewer can take it over or return it to the agent on the Review screen.",
        )
    if s is CaseState.AWAITING_SELLER:
        return (
            "Waiting for the seller to reply.",
            "",
            "If they do not reply, a reminder goes out, and then the case closes.",
        )
    if s is CaseState.AWAITING_OPS:
        ticket = f" (Jira {issues[-1]})" if issues and issues[-1] else ""
        return (
            f"Waiting for the hub to re-measure the parcel{ticket}.",
            "",
            "If the hub does not answer in time, a person takes over.",
        )
    return "The agent is working on it.", "", "This page refreshes by itself while it works."


#: Where an open case is now. Rounds loop (a seller's reply starts the investigation again),
#: so the phase comes from the present state, not from what was ever done.
CURRENT_PHASE = {
    CaseState.RECEIVED: 1,
    CaseState.GATHERING_EVIDENCE: 1,
    CaseState.CALCULATING: 1,
    CaseState.INVESTIGATING: 1,
    CaseState.AWAITING_SELLER: 1,
    CaseState.AWAITING_OPS: 1,
    CaseState.NEEDS_HUMAN_REVIEW: 1,
    CaseState.AWAITING_APPROVAL: 2,
    CaseState.EXECUTING: 3,
    CaseState.VERIFYING: 3,
}


def _phases(
    case: Case, *, investigated: bool, approved: bool, acted: bool
) -> tuple[tuple[str, Mark], ...]:
    if case.closed_at is not None or case.state not in CURRENT_PHASE:
        done = (True, investigated, approved, acted, True)
        # The furthest middle phase that happened: phases before it that did not happen were
        # not needed; phases after it were never reached (the case closed first).
        furthest = max((i for i in (1, 2, 3) if done[i]), default=0)
        return tuple(
            (name, Mark.DONE if flag else (Mark.SKIPPED if i < furthest else Mark.NOT_REACHED))
            for i, (name, flag) in enumerate(zip(PHASES, done, strict=True))
        )
    current = CURRENT_PHASE[case.state]
    s = case.state
    if s is CaseState.NEEDS_HUMAN_REVIEW:
        mark = Mark.PERSON
    elif s in (CaseState.AWAITING_APPROVAL, CaseState.AWAITING_SELLER, CaseState.AWAITING_OPS):
        mark = Mark.WAITING
    else:
        mark = Mark.CURRENT
    return tuple(
        (name, Mark.DONE if i < current else (mark if i == current else Mark.TODO))
        for i, name in enumerate(PHASES)
    )


__all__ = ["PHASES", "Journey", "Mark", "journey"]
