"""Screen 2: a case's trail, step by step, with who did each step (ADR 0044, ADR 0048).

Built only from what the case recorded: its events (I8: every state change with its cause),
each round's evidence, assessment and proposal, its approvals, its action plans and actions.
Nothing is inferred. A step that has no record behind it is not shown. How each step is said
lives in `trail_words`; this module walks the record.

Rounds line up one to one: the n-th `gather_evidence` step is evidence round n, and the
n-th `investigate` step is the n-th proposal. Approvals pair with `apply_approval` steps in
the order they were decided, and actions with `execute` and `verify` steps in action order.

Model-written text (the rationale and findings) is carried as data. The screen shows it as
escaped plain text, "exactly as recorded", never as markup.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    InternalEventType,
)
from dispute_agent.ui import photo_check
from dispute_agent.ui import trail_words as said
from dispute_agent.ui.labels import ACTION, FINDING, Tone, handed_over, words

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.actions import Action, ActionPayload, Approval
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal
    from dispute_agent.kernel.policy.config import PolicyConfig


class Actor(StrEnum):
    CODE = "Code"
    AI = "AI"
    RULES = "Rules"
    PERSON = "Person"
    SELLER = "Seller"
    HUB = "Hub"
    SYSTEM = "System"


class Mark(StrEnum):
    """The step's mark on the timeline (HANDOFF 3.4)."""

    DONE = "done"
    PROPOSE = "propose"
    PERSON = "person"
    CREDITED = "credited"
    CLOSED = "closed"
    ATTENTION = "attention"
    PENDING = "pending"
    MINOR = "minor"


#: The tag a step may carry, by its tone: words, never colour alone.
TAG = {Tone.SETTLED: "Credited", Tone.ATTENTION: "Needs a person", Tone.CLOSED: "Not eligible"}


@dataclass(frozen=True)
class Step:
    at: datetime
    actor: Actor
    title: str
    #: Details: ids, rule codes and facts an investigator needs, folded away.
    lines: tuple[str, ...] = ()
    #: Model-written text, shown literally: the rationale first, then each finding.
    quoted: tuple[str, ...] = ()
    tone: Tone = Tone.DONE
    #: One line a reader takes in at a glance.
    summary: str = ""
    #: The evidence round this step belongs to; 0 is before the first look.
    round: int = 0
    #: The service's own bookkeeping, or the closing "Now: …" line: shown quietly, unnumbered.
    minor: bool = False
    mark: Mark = Mark.DONE
    #: Who proposed, in full, for a proposal's "proposed by" caption.
    proposer: str = ""
    #: When set, every quoted line is shown together under this caption (not a proposal's
    #: "reasoning, then findings"): the photo check's observations (ADR 0049).
    quote_caption: str = ""

    @property
    def tag(self) -> str:
        return "" if self.minor else TAG.get(self.tone, "")


#: Why a shipment was found ineligible, by the pack's reason code (eligibility.py).
INELIGIBLE = {
    "not_delivered": (
        "The scan history shows the shipment was not delivered. Only delivered shipments "
        "can be disputed."
    ),
}
#: Shipment statuses as a seller reads them (the pack's own codes).
STATUS = {
    "DELIVERED": "Delivered",
    "RTO": "Returned to origin",
    "LOST": "Lost",
    "CANCELLED": "Cancelled",
}


@dataclass(frozen=True)
class Recommendation:
    """What the investigator proposed, and on what."""

    action: str
    confidence: str
    by: str
    rationale: str
    findings: tuple[tuple[str, str, tuple[str, ...]], ...] = ()


@dataclass
class Trail:
    case_id: str
    shipment_ref: str
    label: str
    tone: Tone
    is_open: bool
    ai_calls: int = 0
    ai_cost: Decimal = Decimal(0)
    steps: list[Step] = field(default_factory=list)
    #: What started each round: "First look", "After the seller replied", ...
    round_titles: dict[int, str] = field(default_factory=dict)


def recommendation(proposal: Proposal) -> Recommendation:
    if proposal.reasoner == "llm":
        version = f"{proposal.prompt_name} {proposal.prompt_version}".strip()
        by = f"AI ({proposal.model_id or 'model'}, instructions {version})"
    else:
        by = "Rules engine (the AI was off or not needed)"
    if proposal.fallback_used:
        by += ", fallback used"
    return Recommendation(
        action=ACTION[proposal.action],
        confidence=proposal.confidence.value,
        by=by,
        rationale=proposal.rationale,
        findings=tuple(
            (FINDING.get(f.kind.value, words(f.kind.value)), f.statement, tuple(f.evidence_refs))
            for f in proposal.findings
        ),
    )


def _policy_note(to_state: CaseState | None, review: Any, message: bool) -> tuple[str, Tone]:
    if to_state is CaseState.NEEDS_HUMAN_REVIEW and review is not None:
        sentence, _code = handed_over(review.code, getattr(review, "detail", "") or "")
        return f"Policy sent it to a person. {sentence}", Tone.ATTENTION
    if to_state is CaseState.AWAITING_APPROVAL:
        what = "the email " if message else ""
        return f"Policy allows it, but a person must approve {what}first.", Tone.WAITING
    if to_state is CaseState.EXECUTING:
        return "Policy allows it without approval.", Tone.DONE
    return "", Tone.DONE


def _decided(approvals: Sequence[Approval]) -> list[Approval]:
    done = [a for a in approvals if a.decision is not ApprovalDecision.PENDING and a.decided_at]
    return sorted(done, key=lambda a: a.decided_at)  # type: ignore[arg-type, return-value]


def build(
    work: SqliteUnitOfWork,
    case: Case,
    *,
    usage: tuple[int, Decimal] | None = None,
    policy: PolicyConfig | None = None,
) -> Trail:
    from dispute_agent.ui.labels import state

    label, tone = state(case.state)
    trail = Trail(str(case.id), case.facts.shipment_ref, label, tone, case.closed_at is None)
    if usage is not None:
        trail.ai_calls, trail.ai_cost = usage
    proposals = list(work.proposals.for_case(case.id))
    assessments = work.assessments.for_case(case.id)
    every = list(work.approvals.for_case(case.id))
    approvals = _decided(every)
    actions = sorted(work.actions.for_case(case.id), key=lambda a: a.action_seq)
    plans: dict[str, ActionPayload] = {}
    for taken in actions:
        try:
            plans[str(taken.id)] = work.intents.get(taken.intent_id).payload
        except Exception:  # a plan that cannot be read: the step says less, never guesses
            continue
    asked_hub = any(type(p).__name__ == "IssuePayload" for p in plans.values())
    rounds = investigations = decisions = executions = verifications = 0
    after = "Looked again"

    def add(step: Step) -> None:
        trail.steps.append(replace(step, round=rounds))

    def plan(action: Action | None) -> ActionPayload | None:
        return plans.get(str(action.id)) if action is not None else None

    for event in work.events.list_for_case(case.id):
        at, kind, cause, payload = event.occurred_at, event.type, event.cause, event.payload
        if kind == InboundEventType.DISPUTE_RAISED:
            add(Step(at, Actor.SELLER, f"Dispute raised on {case.facts.shipment_ref}"))
        elif kind == InboundEventType.SELLER_REPLY_RECEIVED:
            add(Step(at, Actor.SELLER, "The seller replied"))
            after = "After the seller replied"
        elif kind == InboundEventType.OPS_FINDING_RECEIVED:
            add(Step(at, Actor.HUB, "The hub sent its finding"))
            after = "After the hub's finding"
        elif kind == InboundEventType.TIMER_FIRED:
            add(Step(at, Actor.SYSTEM, "A deadline passed", tone=Tone.WAITING))
            after = "After a deadline passed"
        elif kind in PERSON_STEP:
            add(Step(at, Actor.PERSON, PERSON_STEP[kind]))
            if kind == InboundEventType.HUMAN_RETURNED_CASE:
                after = "After a person returned it"
        elif kind == InternalEventType.CASE_OUTCOME_RECORDED:
            add(
                Step(
                    at,
                    Actor.SYSTEM,
                    "Outcome recorded for pattern detection",
                    minor=True,
                    mark=Mark.MINOR,
                )
            )
        elif kind == InternalEventType.STATE_CHANGED and cause.kind.value == "STEP":
            to_state = getattr(payload, "to_state", None)
            review = getattr(payload, "review_reason", None)
            step = cause.ref
            if step == "eligibility":
                add(_eligibility(at, case, to_state, review))
            elif step == "gather_evidence":
                rounds += 1
                trail.round_titles[rounds] = "First look" if rounds == 1 else after
                try:
                    bundle = work.evidence.bundle(case.id, rounds)
                    summary, lines = said.evidence(bundle, asked_later=asked_hub or rounds > 1)
                    checked = photo_check.latest(bundle.items)
                except Exception:  # a round with no stored evidence says nothing about it
                    summary, lines, checked = "", (), None
                add(Step(at, Actor.CODE, "Gathered the evidence", lines, summary=summary))
                if checked is not None:  # ADR 0049: a person returned a photo case
                    add(_photo_checked(at, checked))
            elif step == "assess":
                found = next((a for a in assessments if a.gather_round == rounds), None)
                closed = to_state is CaseState.REJECTED_INELIGIBLE
                summary, lines = said.recalculation(found, closed_ineligible=closed)
                add(Step(at, Actor.CODE, "Recalculated the charge", lines, summary=summary))
                if closed:  # T05: the evidence proved what intake could not see
                    codes = found.reason_codes if found else ()
                    add(
                        Step(
                            at,
                            Actor.CODE,
                            "Closed: not eligible",
                            (
                                "Rule T05: the scan history overrides the delivered status",
                                *tuple(f"Reason code: {c}" for c in codes),
                            ),
                            tone=Tone.CLOSED,
                            mark=Mark.CLOSED,
                            summary=" ".join(
                                INELIGIBLE.get(c, words(c).capitalize()) for c in codes
                            )
                            or "The evidence showed this shipment cannot be disputed.",
                        )
                    )
            elif step == "investigate":
                proposal = proposals[investigations] if investigations < len(proposals) else None
                investigations += 1
                add(_proposed(at, proposal, to_state, review))
            elif step == "apply_approval":
                approval = approvals[decisions] if decisions < len(approvals) else None
                decisions += 1
                if approval is not None:
                    add(_approval(at, approval))
            elif step == "execute":
                action = actions[executions] if executions < len(actions) else None
                executions += 1
                if action is not None:
                    title, summary, lines = said.executed(action, plan(action))
                    add(Step(at, Actor.CODE, title, lines, summary=summary))
            elif step == "verify":
                action = actions[verifications] if verifications < len(actions) else None
                verifications += 1
                if action is not None:
                    add(_verified(at, action, plan(action), to_state, review))
            elif to_state is CaseState.NEEDS_HUMAN_REVIEW:
                note, _t = _policy_note(to_state, review, message=False)
                add(
                    Step(
                        at, Actor.SYSTEM, "Handed to a person", (note,), minor=True, mark=Mark.MINOR
                    )
                )
    if trail.is_open and trail.steps:
        pending = next((a for a in every if a.decision is ApprovalDecision.PENDING), None)
        line = said.waiting(case.state, pending, policy)
        add(Step(trail.steps[-1].at, Actor.SYSTEM, line, minor=True, mark=Mark.PENDING))
    return trail


def _photo_checked(at: datetime, checked: photo_check.PhotoCheck) -> Step:
    """The vision model's look at the parcel photo: advice for a person, never a decision."""
    if not checked.ok:
        return Step(
            at,
            Actor.AI,
            "Tried to check the parcel photo",
            summary=checked.summary,
            tone=Tone.ATTENTION,
            mark=Mark.ATTENTION,
        )
    rows = (*(f"{k}: {v}" for k, v in checked.rows), f"By {checked.by}.")
    return Step(
        at,
        Actor.AI,
        "Checked the parcel photo",
        rows,
        quoted=checked.observations,
        summary=checked.summary,
        mark=Mark.PROPOSE,
        quote_caption=f"What it saw, exactly as recorded · by {checked.by}",
    )


def _eligibility(at: datetime, case: Case, to_state: CaseState | None, review: Any) -> Step:
    if to_state is CaseState.REJECTED_INELIGIBLE:
        status = str(case.facts.pack_facts.get("shipment_status", ""))
        shown = (
            f'Its status is "{STATUS.get(status, status)}", not delivered.'
            if status
            else ("The shipment's status shows it was not delivered.")
        )
        return Step(
            at,
            Actor.CODE,
            "Checked eligibility: not eligible",
            ("Rule T02: checked at intake",),
            tone=Tone.CLOSED,
            mark=Mark.CLOSED,
            summary=f"{shown} Only delivered shipments can be disputed, so the case closed.",
        )
    if to_state is CaseState.NEEDS_HUMAN_REVIEW:
        note, tone = _policy_note(to_state, review, message=False)
        return Step(
            at,
            Actor.CODE,
            "Checked eligibility: unsure",
            (note,),
            tone=tone,
            mark=Mark.PERSON,
            summary="The shipment's status could not be read, so a person decides.",
        )
    return Step(
        at,
        Actor.CODE,
        "Checked eligibility: eligible",
        summary="Its status says delivered. The evidence is checked next and can still prove otherwise.",
    )


def _proposed(
    at: datetime, proposal: Proposal | None, to_state: CaseState | None, review: Any
) -> Step:
    message = proposal is not None and proposal.action.value in (
        "request_seller_evidence",
        "uphold_charge",
    )
    note, tone = _policy_note(to_state, review, message=message)
    if proposal is None:
        return Step(at, Actor.SYSTEM, "Investigation ended without a proposal", (note,), tone=tone)
    rec = recommendation(proposal)
    code = rec.rationale.partition(":")[0].strip()
    lines = ((f"Rule {code}",) if code[:1] == "B" and code[1:].isdigit() else ()) + tuple(
        f"{kind}: cites {', '.join(refs)}" for kind, _text, refs in rec.findings if refs
    )
    return Step(
        at,
        Actor.AI if proposal.reasoner == "llm" else Actor.RULES,
        f"Proposed: {rec.action}",
        lines,
        (rec.rationale, *tuple(f"{kind}: {text}" for kind, text, _refs in rec.findings)),
        tone=tone,
        mark=Mark.PERSON if tone is Tone.ATTENTION else Mark.PROPOSE,
        summary=f"{rec.confidence.capitalize()} confidence. {note}".strip(),
        proposer=rec.by,
    )


def _approval(at: datetime, approval: Approval) -> Step:
    approver = approval.decided_by.id.removeprefix("human:") if approval.decided_by else ""
    return Step(
        at,
        Actor.PERSON,
        said.approved(approval, approver),
        (
            f"Approver: {approver} (listed in config/policy.yaml)",
            "Bound to the exact text or amount shown on the Review screen",
        ),
        summary=f"Note: {approval.note}" if approval.note else "",
    )


def _verified(
    at: datetime,
    action: Action,
    payload: ActionPayload | None,
    to_state: CaseState | None,
    review: Any,
) -> Step:
    if action.status is ActionStatus.VERIFIED:
        settled = to_state is CaseState.RESOLVED_CREDITED
        return Step(
            at,
            Actor.CODE,
            "Proved it happened by reading it back",
            tone=Tone.SETTLED if settled else Tone.DONE,
            mark=Mark.CREDITED if settled else Mark.DONE,
            summary=said.proved(payload, to_state),
        )
    note, _tone = _policy_note(to_state, review, message=False)
    # Never shown as success: no coral, no check (safety rule 6).
    return Step(
        at,
        Actor.CODE,
        "Could not prove it happened",
        (note,) if note else (),
        tone=Tone.ATTENTION,
        mark=Mark.ATTENTION,
        summary="The read-back did not find it, so a person checks.",
    )


PERSON_STEP: dict[InboundEventType | InternalEventType, str] = {
    InboundEventType.HUMAN_TOOK_CASE: "A person took the case over",
    InboundEventType.HUMAN_RETURNED_CASE: "A person returned the case to the agent",
    InboundEventType.HUMAN_REOPENED_CASE: "A person reopened the case",
    InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL: "A person confirmed the seller's withdrawal",
}


__all__ = [
    "INELIGIBLE",
    "STATUS",
    "TAG",
    "Actor",
    "Mark",
    "Recommendation",
    "Step",
    "Trail",
    "build",
    "recommendation",
]
