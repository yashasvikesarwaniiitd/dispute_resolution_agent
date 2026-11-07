"""Screen 2: a case's trail, step by step, with who did each step (ADR 0044).

Built only from what the case recorded: its events (I8: every state change with its cause),
each round's evidence, assessment and proposal, its approvals and its actions. Nothing is
inferred. A step that has no record behind it is not shown.

Rounds line up one to one: the n-th `gather_evidence` step is evidence round n, and the
n-th `investigate` step is the n-th proposal. Approvals pair with `apply_approval` steps in
the order they were decided, and actions with `execute` and `verify` steps in action order.

Model-written text (the rationale and findings) is carried as data. The screen shows it as
plain text, never as markup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    InternalEventType,
)
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.ui.labels import ACTION, EVIDENCE, FINDING, Tone, state, words

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.actions import Action, Approval
    from dispute_agent.kernel.domain.evidence import ChargeAssessment
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal


class Actor(StrEnum):
    CODE = "Code"
    AI = "AI"
    RULES = "Rules"
    PERSON = "Person"
    SELLER = "Seller"
    HUB = "Hub"
    SYSTEM = "System"


@dataclass(frozen=True)
class Step:
    at: datetime
    actor: Actor
    title: str
    lines: tuple[str, ...] = ()
    #: Model-written text, shown literally (never as markdown).
    quoted: tuple[str, ...] = ()
    tone: Tone = Tone.DONE


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


def _evidence(work: SqliteUnitOfWork, case: Case, round_no: int) -> tuple[str, ...]:
    try:
        bundle = work.evidence.bundle(case.id, round_no)
    except Exception:  # NotFound and its kin: a round with no stored evidence shows nothing
        return ()
    lines = []
    for item in bundle.items:
        key = item.result.requirement_key
        lines.append(f"{EVIDENCE.get(key, key)}: {item.result.status.value.lower()}")
    return tuple(lines)


def _assessment(found: ChargeAssessment | None) -> tuple[str, ...]:
    if found is None:
        return ()
    lines = [f"{fact.label.capitalize()}: {render_fact(fact.value)}" for fact in found.facts]
    if found.definite_errors:
        lines.append("Definite errors: " + ", ".join(words(e) for e in found.definite_errors))
    else:
        lines.append("Definite errors: none found by code")
    return tuple(lines)


def _outcome(to_state: CaseState | None, reason: Any) -> tuple[str, Tone]:
    if to_state is CaseState.NEEDS_HUMAN_REVIEW and reason is not None:
        detail = f" ({reason.detail})" if getattr(reason, "detail", None) else ""
        return f"Policy sent it to a person: {words(reason.code.value)}{detail}", Tone.ATTENTION
    if to_state is CaseState.AWAITING_APPROVAL:
        return "Policy: allowed, but a person must approve first", Tone.WAITING
    if to_state is CaseState.EXECUTING:
        return "Policy: allowed without approval", Tone.DONE
    if to_state is not None:
        return f"Next: {state(to_state)[0]}", Tone.DONE
    return "", Tone.DONE


def _decided(approvals: Sequence[Approval]) -> list[Approval]:
    done = [a for a in approvals if a.decision is not ApprovalDecision.PENDING and a.decided_at]
    return sorted(done, key=lambda a: a.decided_at)  # type: ignore[arg-type, return-value]


def _action_line(action: Action) -> str:
    what = {
        ActionType.SEND_MESSAGE: "Sent an email",
        ActionType.UPSERT_ISSUE: "Opened a ticket for the hub",
        ActionType.POST_CREDIT: "Posted a credit to the ledger",
    }[action.action_type]
    ref = f" ({action.external_ref})" if action.external_ref else ""
    return f"{what}{ref}"


def build(work: SqliteUnitOfWork, case: Case, *, usage: tuple[int, Decimal] | None = None) -> Trail:
    label, tone = state(case.state)
    trail = Trail(
        case_id=str(case.id),
        shipment_ref=case.facts.shipment_ref,
        label=label,
        tone=tone,
        is_open=case.closed_at is None,
    )
    if usage is not None:
        trail.ai_calls, trail.ai_cost = usage
    proposals = list(work.proposals.for_case(case.id))
    assessments = work.assessments.for_case(case.id)
    approvals = _decided(work.approvals.for_case(case.id))
    actions = sorted(work.actions.for_case(case.id), key=lambda a: a.action_seq)
    rounds = investigations = decisions = executions = verifications = 0
    add = trail.steps.append

    for event in work.events.list_for_case(case.id):
        at, kind, cause = event.occurred_at, event.type, event.cause
        payload = event.payload
        if kind == InboundEventType.DISPUTE_RAISED:
            add(Step(at, Actor.SELLER, f"Dispute raised on {case.facts.shipment_ref}"))
        elif kind == InboundEventType.SELLER_REPLY_RECEIVED:
            add(Step(at, Actor.SELLER, "The seller replied", tone=Tone.DONE))
        elif kind == InboundEventType.OPS_FINDING_RECEIVED:
            add(Step(at, Actor.HUB, "The hub sent its finding"))
        elif kind == InboundEventType.TIMER_FIRED:
            add(Step(at, Actor.SYSTEM, "A deadline passed", tone=Tone.WAITING))
        elif kind in (
            InboundEventType.HUMAN_TOOK_CASE,
            InboundEventType.HUMAN_RETURNED_CASE,
            InboundEventType.HUMAN_REOPENED_CASE,
            InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL,
        ):
            add(Step(at, Actor.PERSON, words(kind.value).capitalize()))
        elif kind == InternalEventType.CASE_OUTCOME_RECORDED:
            add(Step(at, Actor.SYSTEM, "Outcome shared with pattern detection"))
        elif kind == InternalEventType.STATE_CHANGED and cause.kind.value == "STEP":
            to_state = getattr(payload, "to_state", None)
            reason = getattr(payload, "review_reason", None)
            step = cause.ref
            if step == "eligibility":
                if to_state is CaseState.REJECTED_INELIGIBLE:
                    add(Step(at, Actor.CODE, "Checked eligibility: not eligible", tone=Tone.CLOSED))
                elif to_state is CaseState.NEEDS_HUMAN_REVIEW:
                    note, t = _outcome(to_state, reason)
                    add(Step(at, Actor.CODE, "Checked eligibility: unsure", (note,), tone=t))
                else:
                    add(Step(at, Actor.CODE, "Checked eligibility: eligible"))
            elif step == "gather_evidence":
                rounds += 1
                add(
                    Step(
                        at,
                        Actor.CODE,
                        f"Gathered evidence (round {rounds})",
                        _evidence(work, case, rounds),
                    )
                )
            elif step == "assess":
                found = next((a for a in assessments if a.gather_round == rounds), None)
                add(Step(at, Actor.CODE, "Recalculated the charge", _assessment(found)))
            elif step == "investigate":
                proposal = proposals[investigations] if investigations < len(proposals) else None
                investigations += 1
                note, t = _outcome(to_state, reason)
                if proposal is None:
                    add(
                        Step(
                            at,
                            Actor.SYSTEM,
                            "Investigation ended without a proposal",
                            (note,),
                            tone=t,
                        )
                    )
                    continue
                rec = recommendation(proposal)
                proposer = Actor.AI if proposal.reasoner == "llm" else Actor.RULES
                said = (f"Confidence: {rec.confidence}", f"By: {rec.by}", note)
                quoted = (
                    rec.rationale,
                    *tuple(f"{kind_}: {text}" for kind_, text, _refs in rec.findings),
                )
                add(Step(at, proposer, f"Proposed: {rec.action}", said, quoted, tone=t))
            elif step == "apply_approval":
                approval = approvals[decisions] if decisions < len(approvals) else None
                decisions += 1
                if approval is None:
                    continue
                approver = (
                    approval.decided_by.id.removeprefix("human:") if approval.decided_by else ""
                )
                what = approval.request.subject.value.lower()
                amount = f" of {approval.request.amount}" if approval.request.amount else ""
                noted: tuple[str, ...] = (f"Note: {approval.note}",) if approval.note else ()
                add(
                    Step(
                        at,
                        Actor.PERSON,
                        f"{approver or 'A person'} {approval.decision.value.lower()} "
                        f"the {what}{amount}",
                        noted,
                        tone=Tone.DONE,
                    )
                )
            elif step == "execute":
                action = actions[executions] if executions < len(actions) else None
                executions += 1
                if action is not None:
                    add(Step(at, Actor.CODE, _action_line(action)))
            elif step == "verify":
                action = actions[verifications] if verifications < len(actions) else None
                verifications += 1
                if action is None:
                    continue
                if action.status is ActionStatus.VERIFIED:
                    settled = to_state is CaseState.RESOLVED_CREDITED
                    add(
                        Step(
                            at,
                            Actor.CODE,
                            "Proved it happened by reading it back",
                            (f"Next: {state(to_state)[0]}",) if to_state else (),
                            tone=Tone.SETTLED if settled else Tone.DONE,
                        )
                    )
                else:
                    note, t = _outcome(to_state, reason)
                    add(
                        Step(
                            at,
                            Actor.CODE,
                            "Could not prove it happened",
                            (note,),
                            tone=Tone.ATTENTION,
                        )
                    )
            elif to_state is CaseState.NEEDS_HUMAN_REVIEW:
                note, t = _outcome(to_state, reason)
                add(Step(at, Actor.SYSTEM, "Handed to a person", (note,), tone=t))
    return trail


__all__ = ["Actor", "Recommendation", "Step", "Trail", "build", "recommendation"]
