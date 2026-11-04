"""Transition effects: what a move does besides changing the state (transitions.yaml, I4, I8).

The state machine is pure, so it changes the case row and hands everything else back on
`Applied.effects`. Until this module existed nothing carried those out, and three of them
matter for safety, not tidiness:

- **mark_in_flight_unknown** (T90). A case sent to a person while an action was PLANNED or
  SENT may already have done that thing. Leaving the row PLANNED or SENT would let a later
  pass treat it as not started, or as fine; UNKNOWN makes a person resolve it first (I4, T37).
- **supersede_pending** (T13-T16, T90). A plan waiting for approval on a case that has moved
  on must not stay approvable. The reviewer would be deciding something the case no longer
  wants, and the console would show it as current.
- **cancel_timers**, including voiding timers that fired but were not yet handled, so a late
  delivery is recognised as stale (pre-dispatch rule P1) instead of acting on an old deadline.

Applied in the order transitions.yaml lists, inside the same unit of work as the transition,
so the move and its consequences are saved together or not at all. Each one writes an event:
the audit trail says what was cancelled, superseded or marked, and why.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.actions import Timer
from dispute_agent.kernel.domain.enums import (
    RESOLVED_STATES,
    ActionStatus,
    ActionType,
    ApprovalDecision,
    CauseKind,
    IntentStatus,
    InternalEventType,
    ReviewCode,
    TimerKind,
)
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.events import Event, StepPayload
from dispute_agent.kernel.domain.ids import EventId, OutcomeId, TimerId
from dispute_agent.kernel.domain.models import Cause
from dispute_agent.kernel.domain.patterns import CaseOutcome
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.statemachine.machine import SYSTEM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, ActionIntent, Approval
    from dispute_agent.kernel.domain.ids import (
        ActionId,
        ApprovalId,
        AssessmentId,
        CaseId,
        IntentId,
    )
    from dispute_agent.kernel.domain.models import Actor, Case
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import IdGenerator
    from dispute_agent.kernel.statemachine.machine import Applied

STEP = "transition_effects"

#: Actions that may have reached the outside world without a confirmed result (T90).
IN_FLIGHT = (ActionStatus.PLANNED, ActionStatus.SENT)

#: Intents whose plan has not become an action yet: superseding them is still meaningful.
SUPERSEDABLE = (IntentStatus.PENDING_APPROVAL, IntentStatus.APPROVED)


class EffectTimers(Protocol):
    def schedule(self, timer: Timer) -> Timer: ...
    def cancel_kinds(
        self, case_id: CaseId, kinds: Sequence[TimerKind], now: datetime, reason: str
    ) -> int: ...
    def void_fired(self, case_id: CaseId, kinds: Sequence[TimerKind], now: datetime) -> int: ...


class EffectIntents(Protocol):
    def get(self, intent_id: IntentId) -> ActionIntent: ...
    def set_status(self, intent_id: IntentId, status: IntentStatus) -> None: ...


class EffectApprovals(Protocol):
    def for_intent(self, intent_id: IntentId) -> Approval | None: ...
    def decide(
        self,
        approval_id: ApprovalId,
        decision: ApprovalDecision,
        by: Actor | None,
        at: datetime,
        note: str,
    ) -> Approval: ...


class EffectActions(Protocol):
    def for_case(self, case_id: CaseId) -> Sequence[Action]: ...
    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action: ...


class OutcomeOutbox(Protocol):
    def add(self, outcome: CaseOutcome, now: datetime) -> None: ...


@dataclass
class TransitionEffects:
    """Carries out `Applied.effects` against the stores of one unit of work."""

    timers: EffectTimers
    intents: EffectIntents
    approvals: EffectApprovals
    actions: EffectActions
    ids: IdGenerator
    config: PolicyConfig
    outcomes: OutcomeOutbox | None = None

    def __call__(self, before: Case, after: Case, applied: Applied) -> list[Event]:
        """Apply the effects of the move `before` -> `after`; return the events they wrote.

        `after` is the case as saved (its new version), which is what a timer is armed at:
        pre-dispatch rule P1 compares a fired timer's `armed_at_version` with the case.
        """
        effects = applied.effects
        now = applied.event.occurred_at
        cause = applied.event.id
        events: list[Event] = []
        reason = f"{applied.transition.id}:{applied.event.payload.model_dump().get('trigger')}"

        cancel = list(TimerKind) if effects.cancel_all_timers else list(effects.cancel_timers)
        if cancel:
            cancelled = self.timers.cancel_kinds(after.id, cancel, now, reason)
            voided = self.timers.void_fired(after.id, cancel, now)
            if cancelled or voided:
                events.append(
                    self._event(
                        after,
                        InternalEventType.TIMER_CANCELLED,
                        f"{','.join(k.value for k in cancel)} (cancelled {cancelled}, "
                        f"voided {voided}) by {applied.transition.id}",
                        now,
                        cause,
                    )
                )

        for kind in effects.schedule_timers:
            events.append(self._arm(after, kind, now, cause))

        if effects.supersede_pending and before.pending_intent_id is not None:
            events.extend(self._supersede(after, before.pending_intent_id, now, cause))

        if effects.mark_in_flight_unknown:
            events.extend(self._mark_unknown(after, now, cause))

        if effects.record_outcome:
            events.append(self._record_outcome(after, now, cause))
        return events

    # -- the effects -------------------------------------------------------------------------

    def _arm(self, case: Case, kind: TimerKind, now: datetime, cause: EventId) -> Event:
        due = now + self._delay(kind)
        # At most one active timer per (case, kind). A clarification request re-opens the
        # seller window (T18), so whatever is still armed of the same kind is replaced.
        self.timers.cancel_kinds(case.id, [kind], now, "rearmed")
        self.timers.schedule(
            Timer(
                id=TimerId(self.ids.new_id("tmr")),
                case_id=case.id,
                kind=kind,
                due_at=due,
                anchor_at=now,
                armed_in_state=case.state,
                armed_at_version=case.version,
                correlation_id=case.correlation_id,
                created_at=now,
            )
        )
        return self._event(
            case,
            InternalEventType.TIMER_SCHEDULED,
            f"{kind.value} due {due.isoformat()}",
            now,
            cause,
        )

    def _delay(self, kind: TimerKind) -> timedelta:
        timers, approvals = self.config.timers, self.config.approvals
        delays = {
            TimerKind.SELLER_REMINDER: timedelta(hours=timers.reminder_after_hours),
            TimerKind.SELLER_DECISION: timedelta(hours=timers.decision_after_hours),
            TimerKind.OPS_TIMEOUT: timedelta(hours=timers.ops_timeout_hours),
            TimerKind.APPROVAL_EXPIRY: timedelta(hours=approvals.expire_after_hours),
        }
        if kind not in delays:
            # VERIFY_RETRY and EVIDENCE_RETRY are armed by their steps, which know the time.
            raise ConfigError(f"no transition may schedule {kind.value}; its step arms it")
        return delays[kind]

    def _supersede(
        self, case: Case, intent_id: IntentId, now: datetime, cause: EventId
    ) -> list[Event]:
        intent = self.intents.get(intent_id)
        if intent.status not in SUPERSEDABLE:
            # Already an action (or already closed): the in-flight rule covers the action.
            return []
        expired = (
            case.review_reason is not None
            and case.review_reason.code is ReviewCode.APPROVAL_EXPIRED
        )
        self.intents.set_status(
            intent_id, IntentStatus.EXPIRED if expired else IntentStatus.SUPERSEDED
        )
        approval = self.approvals.for_intent(intent_id)
        if approval is not None and approval.decision is ApprovalDecision.PENDING:
            self.approvals.decide(
                approval.id,
                ApprovalDecision.EXPIRED if expired else ApprovalDecision.SUPERSEDED,
                None,
                now,
                "the case moved on before a decision",
            )
        word = "expired" if expired else "superseded"
        return [
            self._event(
                case,
                InternalEventType.APPROVAL_SUPERSEDED,
                f"plan {intent_id} {word}" + (f", approval {approval.id}" if approval else ""),
                now,
                cause,
            )
        ]

    def _mark_unknown(self, case: Case, now: datetime, cause: EventId) -> list[Event]:
        events: list[Event] = []
        for action in self.actions.for_case(case.id):
            if action.status not in IN_FLIGHT:
                continue
            self.actions.set_status(
                action.id,
                ActionStatus.UNKNOWN,
                now,
                last_error_code="in_flight_at_escalation",
                last_error_detail=f"was {action.status.value} when the case went to a person",
            )
            events.append(
                self._event(
                    case,
                    InternalEventType.ACTION_UNKNOWN,
                    f"{action.id} was {action.status.value}: in_flight_at_escalation",
                    now,
                    cause,
                )
            )
        return events

    def _record_outcome(self, case: Case, now: datetime, cause: EventId) -> Event:
        credited, assessment_id = self._credited(case)
        outcome = CaseOutcome(
            id=OutcomeId(self.ids.new_id("out")),
            case_id=case.id,
            state=case.state,
            resolved=case.state in RESOLVED_STATES,
            facts=case.facts,
            assessment_id=assessment_id,
            credited=credited,
            closed_at=case.closed_at or now,
            reopen_count=case.reopen_count,
        )
        if self.outcomes is not None:
            self.outcomes.add(outcome, now)
        return self._event(
            case,
            InternalEventType.CASE_OUTCOME_RECORDED,
            f"{case.state.value}" + (f", credited {credited.amount}" if credited else ""),
            now,
            cause,
        )

    def _credited(self, case: Case) -> tuple[Money | None, AssessmentId | None]:
        total = 0
        assessment_id: AssessmentId | None = None
        for action in self.actions.for_case(case.id):
            if action.action_type is not ActionType.POST_CREDIT:
                continue
            if action.status is not ActionStatus.VERIFIED:
                continue
            intent = self.intents.get(action.intent_id)
            amount = getattr(intent.payload, "amount", None)
            if isinstance(amount, Money):
                total += amount.to_paise()
                assessment_id = intent.assessment_id
        return (Money.from_paise(total) if total else None), assessment_id

    # -- helpers -----------------------------------------------------------------------------

    def _event(
        self,
        case: Case,
        kind: InternalEventType,
        detail: str,
        now: datetime,
        cause: EventId,
    ) -> Event:
        return Event(
            id=EventId(self.ids.new_id("evt")),
            case_id=case.id,
            type=kind,
            occurred_at=now,
            recorded_at=now,
            actor=SYSTEM,
            cause=Cause(kind=CauseKind.STEP, ref=STEP),
            correlation_id=case.correlation_id,
            causation_id=cause,
            payload=StepPayload(step=STEP, detail=detail[:500]),
        )


__all__ = ["IN_FLIGHT", "STEP", "OutcomeOutbox", "TransitionEffects"]
