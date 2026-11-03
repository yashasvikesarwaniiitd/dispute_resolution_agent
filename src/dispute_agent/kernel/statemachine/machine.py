"""`StateMachine.apply` and the acceptance matrix (ADR 0004, ADR 0005, I8).

The shape of this module is the invariant. `apply` returns the new case **and** the event
that records why it moved, as one value — there is no way to get a moved case without also
getting its event, so "every state change writes an event carrying its cause" is not a rule
the caller has to remember (I8).

It is a pure function of explicit inputs (CLAUDE.md section 6). Guards arrive as a mapping of
already-evaluated booleans rather than as callables with access to the world, because the
step that calls `apply` is the thing that knows whether an approval matches a pending intent
— and keeping that knowledge out of here is what lets the whole machine be tested without a
database.

A guard that was not supplied is a `ConfigError`, never an assumed `True`. The guards on the
credit path are the ones that would be assumed, and `intent_is_not_credit` quietly passing
would be I3 defeated by an omission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import TERMINAL_STATES as _TERMINAL
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    InternalEventType,
    TimerKind,
    Trigger,
)
from dispute_agent.kernel.domain.errors import (
    ConfigError,
    GuardFailed,
    IllegalTransition,
)
from dispute_agent.kernel.domain.events import Event, EventRejectedPayload, StateChangedPayload
from dispute_agent.kernel.domain.models import Actor, Case, ReviewReason

if TYPE_CHECKING:
    from collections.abc import Mapping
    from datetime import datetime

    from dispute_agent.kernel.domain.ids import CorrelationId, EventId
    from dispute_agent.kernel.domain.models import Cause
    from dispute_agent.kernel.statemachine.transitions import Effects, Transition, TransitionTable

SYSTEM = Actor(kind=ActorKind.SYSTEM, id="system")

#: What the acceptance matrix can say about an inbound event, before any transition.
REACTION_ABSORB = "absorb"
REACTION_DEFER = "defer"
REACTION_IGNORE = "ignore"


@dataclass(frozen=True)
class Reaction:
    """How a case in a given state answers an inbound event type.

    `kind` is one of absorb, defer, ignore, step or transition. The matrix sometimes offers
    two, separated by "|" — "transition:seller_reply_received | defer" means take the
    transition if it can, otherwise hold the event — so `alternatives` keeps both in order
    rather than collapsing them here, where the context to choose is not available.
    """

    raw: str
    alternatives: tuple[str, ...]

    @property
    def primary(self) -> str:
        return self.alternatives[0]

    @property
    def kind(self) -> str:
        return self.primary.split(":", 1)[0]

    @property
    def argument(self) -> str:
        """The step name, or the trigger for a transition, with any `(reason)` stripped."""
        if ":" not in self.primary:
            return ""
        tail = self.primary.split(":", 1)[1]
        return tail.split("(", 1)[0]

    @property
    def reason(self) -> str:
        """`transition:escalate(ops_timeout)` carries the review reason in brackets."""
        if "(" not in self.primary:
            return ""
        return self.primary.split("(", 1)[1].rstrip(")")

    @property
    def is_terminalish(self) -> bool:
        return self.kind in (REACTION_ABSORB, REACTION_IGNORE)


@dataclass(frozen=True)
class Applied:
    """The result of a legal move: the case, the event that records it, and what to do next.

    Returned as one value on purpose. A caller cannot take the moved case and forget the
    event, because they arrive together (I8).
    """

    case: Case
    event: Event
    transition: Transition

    @property
    def effects(self) -> Effects:
        """Timers, counters and outcome rows. Carried out by the step, inside its
        transaction, because they need stores the state machine does not have."""
        return self.transition.effects


class StateMachine:
    """Decides whether a move is legal, and what it does. Holds no state of its own."""

    def __init__(self, table: TransitionTable) -> None:
        self._table = table

    @property
    def table(self) -> TransitionTable:
        return self._table

    def react(self, state: CaseState, event_type: str) -> Reaction:
        """What a case in this state does with this inbound event type.

        Total over every (state, event type) pair — the table refuses to build otherwise —
        so there is no inbound event this returns nothing for.
        """
        raw = self._table.reaction(state, event_type)
        return Reaction(raw=raw, alternatives=tuple(part.strip() for part in raw.split("|")))

    def apply(
        self,
        case: Case,
        trigger: Trigger,
        cause: Cause,
        now: datetime,
        event_id: EventId,
        causation_id: EventId,
        *,
        guards: Mapping[str, bool] | None = None,
        actor: Actor = SYSTEM,
        review_reason: ReviewReason | None = None,
        correlation_id: CorrelationId | None = None,
    ) -> Applied:
        """Move the case, or raise.

        Raises IllegalTransition when no row allows the move at all, and GuardFailed when a
        row exists but its guards do not hold. The two are different answers: the first means
        the trigger makes no sense here, the second that it makes sense but the conditions
        are not met, and a caller handles them differently.

        `causation_id` is required, not optional. events-catalog.md section 1 says an internal
        event names the input event whose handling produced it, and the Event model refuses
        one without it — so the caller has to know what it is answering. That is I8 pushing
        back on a signature, and it is the right place for it to push.
        """
        supplied = dict(guards or {})
        candidates = self._table.candidates(case.state, trigger)
        if not candidates:
            raise IllegalTransition(case.state, trigger, "no transition leaves this state on it")

        chosen, failed = self._choose(candidates, supplied)
        if chosen is None:
            raise GuardFailed(case.state, trigger, f"guards did not hold: {list(failed)}")

        moved = self._move(case, chosen, now, review_reason)
        event = Event(
            id=event_id,
            case_id=case.id,
            type=InternalEventType.STATE_CHANGED,
            occurred_at=now,
            recorded_at=now,
            actor=actor,
            cause=cause,
            correlation_id=correlation_id or case.correlation_id,
            causation_id=causation_id,
            payload=StateChangedPayload(
                from_state=case.state,
                to_state=chosen.to_state,
                transition_id=chosen.id,
                trigger=trigger,
                review_reason=moved.review_reason,
            ),
        )
        return Applied(case=moved, event=event, transition=chosen)

    def record_rejection(
        self,
        case: Case,
        event_type: str,
        reaction: Reaction,
        cause: Cause,
        now: datetime,
        event_id: EventId,
        causation_id: EventId,
    ) -> Event:
        """An inbound event the matrix declined. Recorded, never dropped.

        "We ignored this" is a fact about the case. A seller who wrote to a closed case and
        got silence deserves better than an audit trail that does not mention it.
        """
        internal = (
            InternalEventType.EVENT_DEFERRED
            if reaction.kind == REACTION_DEFER
            else InternalEventType.EVENT_IGNORED
        )
        return Event(
            id=event_id,
            case_id=case.id,
            type=internal,
            occurred_at=now,
            recorded_at=now,
            actor=SYSTEM,
            cause=cause,
            correlation_id=case.correlation_id,
            causation_id=causation_id,
            payload=EventRejectedPayload(
                event_type=event_type, state=case.state, reaction=reaction.raw
            ),
        )

    def _choose(
        self, candidates: tuple[Transition, ...], supplied: Mapping[str, bool]
    ) -> tuple[Transition | None, tuple[str, ...]]:
        """First candidate whose guards all hold. Declaration order is the tie-break."""
        failed: tuple[str, ...] = ()
        for candidate in candidates:
            missing = [g for g in candidate.guards if g not in supplied]
            if missing:
                raise ConfigError(
                    f"transition {candidate.id} needs guard(s) {missing} and none were "
                    "supplied; a guard is never assumed to hold"
                )
            unmet = tuple(g for g in candidate.guards if not supplied[g])
            if not unmet:
                return candidate, ()
            failed = failed or unmet
        return None, failed

    def _move(
        self,
        case: Case,
        transition: Transition,
        now: datetime,
        review_reason: ReviewReason | None,
    ) -> Case:
        """Apply the state change and the effects that live on the case row.

        Timers and outcome rows are not here: they need stores, and a pure function cannot
        have them. `Applied.effects` hands them to the step.
        """
        effects = transition.effects
        target = transition.to_state

        reason = case.review_reason
        if effects.set_review_reason:
            if review_reason is None:
                raise ConfigError(
                    f"transition {transition.id} sets a review reason but none was given; "
                    "a case with a person needs to say why (I6)"
                )
            reason = review_reason
        if effects.clear_review_reason or target is not CaseState.NEEDS_HUMAN_REVIEW:
            reason = None
        if target is CaseState.NEEDS_HUMAN_REVIEW and reason is None:
            raise ConfigError(
                f"transition {transition.id} enters NEEDS_HUMAN_REVIEW without a reason"
            )

        updates: dict[str, object] = {
            "state": target,
            "state_entered_at": now,
            "updated_at": now,
            "review_reason": reason,
            "closed_at": now if target in _TERMINAL else None,
        }
        if effects.clear_pending:
            updates["pending_intent_id"] = None
        for counter in effects.increment:
            updates[counter] = getattr(case, counter) + 1
        if target is CaseState.GATHERING_EVIDENCE:
            # rejection_count resets on every entry (kernel-interfaces.md 4.3), and a new
            # round of gathering is a new round for the D41 retry limit.
            updates["rejection_count"] = 0
            updates["gather_round"] = case.gather_round + 1

        return case.model_copy(update=updates)


def timers_to_cancel(effects: Effects, armed: tuple[TimerKind, ...]) -> tuple[TimerKind, ...]:
    """Which armed timers a transition cancels. `cancel_timers: all` means all of them."""
    if effects.cancel_all_timers:
        return armed
    return tuple(kind for kind in armed if kind in effects.cancel_timers)
