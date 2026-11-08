"""Where a case is, and what happens next, in one sentence each (ADR 0044, Agent steps).

The top of the Agent steps screen: a headline, the next step, and five phases - raised,
investigated, approved, acted, closed - marked done, current, waiting, needing a person or
skipped. Every sentence is chosen from the case's state and its records (pending approval,
verified credit, the hub's ticket, the review reason); nothing is inferred and no figure is
computed here - amounts come from the approval records, as stored.
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
from dispute_agent.ui.labels import Tone, state, words

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
    SKIPPED = "skipped"
    TODO = "todo"


@dataclass(frozen=True)
class Journey:
    headline: str
    next_step: str
    tone: Tone
    phases: tuple[tuple[str, Mark], ...]
    #: Code's latest figures for the case - charged, correct, overcharged - as rendered facts;
    #: empty until code has recalculated. Never computed here.
    figures: tuple[tuple[str, str], ...] = ()


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
    headline, next_step = _words(case, pending, credited, issues)
    latest = work.assessments.latest(case.id)
    facts = {f.fact_id: render_fact(f.value) for f in (latest.facts if latest else ())}
    figures = tuple((label, facts[key]) for key, label in FIGURES if key in facts)
    return Journey(
        headline=headline,
        next_step=next_step,
        tone=state(case.state)[1],
        phases=_phases(
            case,
            investigated=bool(list(work.proposals.for_case(case.id))),
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
) -> tuple[str, str]:
    s = case.state
    if s is CaseState.RESOLVED_CREDITED:
        amount = credited[-1].request.amount if credited else None
        return (
            f"Resolved: the seller was credited {render_fact(amount)}."
            if amount
            else "Resolved: the seller was credited.",
            "Nothing left to do. The credit was proved by reading it back from the ledger.",
        )
    if s is CaseState.RESOLVED_UPHELD:
        return (
            "Resolved: the charge was correct, and the seller was told why.",
            "Nothing left to do. The seller can reply with new evidence within 7 days.",
        )
    if s is CaseState.REJECTED_INELIGIBLE:
        return (
            "Closed: this shipment could not be disputed.",
            "Nothing left to do. Only delivered shipments can be disputed.",
        )
    if s is CaseState.WITHDRAWN:
        return "Closed: the seller withdrew the dispute.", "Nothing left to do."
    if s is CaseState.CLOSED_NO_RESPONSE:
        return (
            "Closed: the seller did not reply.",
            "If they reply later, the case opens again and is looked at fresh.",
        )
    if s is CaseState.HANDED_TO_HUMAN:
        return (
            "A person took this case over. The agent has stopped.",
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
            "Approve or reject it on the Review screen. Nothing is sent until then.",
        )
    if s is CaseState.NEEDS_HUMAN_REVIEW:
        reason = case.review_reason
        why = words(reason.code.value) if reason else "a reason the agent recorded"
        detail = f" ({reason.detail})" if reason and reason.detail else ""
        return (
            f"With a person: {why}{detail}.",
            "A reviewer can take it over or return it to the agent on the Review screen.",
        )
    if s is CaseState.AWAITING_SELLER:
        return (
            "Waiting for the seller to reply.",
            "If they do not reply, a reminder goes out, and then the case closes.",
        )
    if s is CaseState.AWAITING_OPS:
        ticket = f" (Jira {issues[-1]})" if issues and issues[-1] else ""
        return (
            f"Waiting for the hub to re-measure the parcel{ticket}.",
            "If the hub does not answer in time, a person takes over.",
        )
    return "The agent is working on it.", "This page refreshes by itself while it works."


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
        return tuple(
            (name, Mark.DONE if flag else Mark.SKIPPED)
            for name, flag in zip(PHASES, done, strict=True)
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
