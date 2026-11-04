"""Dispatch: an inbound event finds its case, and the acceptance matrix says what it does.

orchestrator.md section 3.1, transitions.yaml `accept` and pre-dispatch rules P1-P4. Every
event that is not a new dispute comes through here: seller replies, withdrawals, delivery
failures, ops findings, approval decisions, fired timers and a person's actions.

Three properties the code below is built around:

- **Nothing is dropped.** An event with no case is UNROUTABLE and stays in the inbox for a
  person; an event the matrix ignores is still appended with EVENT_IGNORED and its reason; an
  event the state cannot take yet is DEFERRED and redelivered when the case moves on.
- **Outside text never moves a case by what it says (I5).** A reply moves a case because a
  reply arrived, and the matrix allows it in that state; what the reply says is read later,
  as evidence, by the interpreter, and can only add caution.
- **One path for moves.** Transitions go through `Orchestrator.apply_trigger` / `escalate` /
  `run_named` / `advance`, which all land through `Orchestrator.land` with their effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.enums import (
    TERMINAL_STATES,
    ActionStatus,
    ActorKind,
    ApprovalDecision,
    CaseState,
    CauseKind,
    IntentSource,
    InternalEventType,
    ReviewCode,
    TimerKind,
    Trigger,
)
from dispute_agent.kernel.domain.errors import IllegalTransition, NotFound
from dispute_agent.kernel.domain.events import Event, EventPayload, EventRejectedPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId, InboxId
from dispute_agent.kernel.domain.inbound import (
    HUMAN_EVENTS,
    ApprovalDecided,
    DeliveryFailureReceived,
    DisputeWithdrawn,
    HumanReturnedCase,
    HumanTookCase,
    OpsFindingReceived,
    SellerReplyReceived,
    TimerFired,
    parse_inbound,
    reaction_key,
)
from dispute_agent.kernel.domain.models import Cause, ReviewReason
from dispute_agent.kernel.orchestrator.orchestrator import AUTO_STATES

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, ActionIntent, Approval, Timer
    from dispute_agent.kernel.domain.events import InboundEvent
    from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, IntentId, TimerId
    from dispute_agent.kernel.domain.models import Actor, Case
    from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import Clock, EventLog, IdGenerator
    from dispute_agent.kernel.statemachine.machine import StateMachine

#: Cases a late reply or finding may still be routed to (T39-T44 decide what happens).
SOFT_TERMINAL = (CaseState.CLOSED_NO_RESPONSE, CaseState.RESOLVED_UPHELD)
OPEN = tuple(s for s in CaseState if s not in TERMINAL_STATES)


# -- ports -------------------------------------------------------------------------------------


class Inbox(Protocol):
    def get(self, inbox_id: InboxId) -> dict[str, object]: ...
    def mark_processed(self, inbox_id: InboxId, case_id: CaseId, event_id: EventId) -> None: ...
    def mark_deferred(self, inbox_id: InboxId, case_id: CaseId, event_id: EventId) -> None: ...
    def mark_unroutable(self, inbox_id: InboxId, reason: str) -> None: ...
    def deferred_for_case(self, case_id: CaseId) -> list[dict[str, object]]: ...


class Cases(Protocol):
    def get(self, case_id: CaseId) -> Case: ...
    def save(self, case: Case, expected_version: int) -> Case: ...
    def find_open(self, shipment_ref: str, claim_ref: str) -> Case | None: ...
    def list_in_states(self, states: Collection[CaseState], limit: int = 500) -> Sequence[Case]: ...


class Timers(Protocol):
    def get(self, timer_id: TimerId) -> Timer: ...
    def consume(self, timer_id: TimerId, now: datetime) -> None: ...


class Intents(Protocol):
    def get(self, intent_id: IntentId) -> ActionIntent: ...


class Approvals(Protocol):
    def get(self, approval_id: ApprovalId) -> Approval: ...
    def decide(
        self,
        approval_id: ApprovalId,
        decision: ApprovalDecision,
        by: Actor | None,
        at: datetime,
        note: str,
    ) -> Approval: ...


class Actions(Protocol):
    def for_case(self, case_id: CaseId) -> Sequence[Action]: ...
    def find_by_external_ref(self, external_ref: str) -> Action | None: ...
    def has_verified_credit(self, case_id: object) -> bool: ...
    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action: ...


class Messages(Protocol):
    def record_inbound(
        self, message_id: str, case_id: CaseId, inbox_id: InboxId, reply: SellerReplyReceived
    ) -> bool: ...
    def case_for_conversation(self, conversation_id: str) -> CaseId | None: ...
    def inbound_after(self, case_id: CaseId, after: datetime) -> int: ...


@dataclass(frozen=True)
class DispatchResult:
    inbox_id: InboxId
    case_id: CaseId | None
    #: absorb | ignore:<reason> | defer | transition:<trigger> | escalate:<code> | step:<name>
    #: | unroutable:<reason> | already:<status>
    reaction: str
    state: CaseState | None = None


# -- the dispatcher ----------------------------------------------------------------------------


@dataclass
class Dispatcher:
    orchestrator: Orchestrator
    machine: StateMachine
    inbox: Inbox
    cases: Cases
    events: EventLog
    timers: Timers
    intents: Intents
    approvals: Approvals
    actions: Actions
    messages: Messages
    clock: Clock
    ids: IdGenerator
    config: PolicyConfig

    def dispatch(self, inbox_id: InboxId) -> DispatchResult:
        row = self.inbox.get(inbox_id)
        status = str(row["status"])
        if status in ("PROCESSED", "UNROUTABLE", "FAILED"):
            return DispatchResult(inbox_id, _case_id(row), f"already:{status}")
        event = parse_inbound(str(row["event_json"]))

        case, why = self._route(event, row)
        if case is None:
            self.inbox.mark_unroutable(inbox_id, why)
            return DispatchResult(inbox_id, None, f"unroutable:{why}")

        recorded = self._input_event(row, event, case, inbox_id)
        stale = self._pre_dispatch(case, event)
        if stale is not None:
            self._ignore(case, event, recorded, inbox_id, stale)
            return DispatchResult(inbox_id, case.id, f"ignore:{stale}", case.state)

        result = self._react(case, event, recorded, inbox_id)
        self._redeliver(result.case_id)
        return result

    # -- routing (orchestrator.md section 2) ----------------------------------------------------

    def _route(self, event: InboundEvent, row: Mapping[str, object]) -> tuple[Case | None, str]:
        known = _case_id(row) or event.case_id
        payload = event.payload
        if isinstance(payload, TimerFired):
            try:
                known = self.timers.get(payload.timer_id).case_id
            except NotFound:
                return None, "unknown_timer"
        if known is not None:
            try:
                return self.cases.get(known), "case_id"
            except NotFound:
                return None, "unknown_case"
        if isinstance(payload, SellerReplyReceived):
            return self._route_reply(payload)
        if isinstance(payload, DeliveryFailureReceived | OpsFindingReceived):
            ref = (
                payload.failed_external_message_id
                if isinstance(payload, DeliveryFailureReceived)
                else payload.finding.issue_key
            )
            action = self.actions.find_by_external_ref(ref)
            if action is None:
                return None, "no_action_for_reference"
            return self.cases.get(action.case_id), "action_reference"
        if isinstance(payload, DisputeWithdrawn):
            found = self.cases.find_open(payload.shipment_ref, payload.claim_ref)
            return found, "shipment_and_claim" if found else "no_open_case"
        return None, "no_routing_rule"

    def _route_reply(self, reply: SellerReplyReceived) -> tuple[Case | None, str]:
        if reply.conversation_id:
            case_id = self.messages.case_for_conversation(reply.conversation_id)
            if case_id is not None:
                return self.cases.get(case_id), "conversation"
        sender = reply.from_addr.strip().lower()
        subject = reply.subject.text

        def matching(states: Collection[CaseState]) -> list[Case]:
            return [
                c
                for c in self.cases.list_in_states(states)
                if c.facts.seller_contact.strip().lower() == sender
                and c.facts.shipment_ref in subject
                and c.superseded_by_case_id is None
            ]

        # An open case first; a late reply to a soft-closed case is routed to it so the reopen
        # rules (T39-T44) can decide - never dropped, never guessed between two cases.
        for states, how in ((OPEN, "contact_and_shipment"), (SOFT_TERMINAL, "late_reply")):
            found = matching(states)
            if len(found) == 1:
                return found[0], how
            if len(found) > 1:
                return None, f"ambiguous:{len(found)}_cases"
        return None, "unknown_sender_or_shipment"

    # -- pre-dispatch rules P1-P4, plus the two that need the case --------------------------

    def _pre_dispatch(self, case: Case, event: InboundEvent) -> str | None:
        payload = event.payload
        if case.superseded_by_case_id is not None:
            return "superseded_case"  # P4
        if event.type in HUMAN_EVENTS and event.actor.kind is not ActorKind.HUMAN:
            return "not_human"  # P3
        if isinstance(payload, TimerFired):  # P1
            timer = self.timers.get(payload.timer_id)
            if (
                timer.case_id != case.id
                or timer.cancelled_at is not None
                or timer.voided_at is not None
                or timer.consumed_at is not None
                or timer.fired_at is None
            ):
                return "stale_timer"
        if isinstance(payload, ApprovalDecided):  # P2 and the hash check (I13)
            try:
                approval = self.approvals.get(payload.approval_id)
            except NotFound:
                return "stale_approval"
            if (
                case.pending_intent_id is None
                or approval.request.intent_id != case.pending_intent_id
                or approval.decision is not ApprovalDecision.PENDING
            ):
                return "stale_approval"
            if payload.payload_hash_seen != approval.request.payload_hash:
                return "payload_hash_mismatch"
        if isinstance(payload, DisputeWithdrawn) and (
            payload.authenticated_principal != case.facts.seller_ref
        ):
            return "principal_mismatch"
        return None

    # -- the matrix ---------------------------------------------------------------------------

    def _react(
        self, case: Case, event: InboundEvent, recorded: Event, inbox_id: InboxId
    ) -> DispatchResult:
        reaction = self.machine.react(case.state, reaction_key(event))
        if isinstance(event.payload, TimerFired) and reaction.primary != "defer":
            # Consumed before the reaction runs (orchestrator.md 3.1): a transition it causes
            # may cancel timers of its own kind, and a timer cannot be both voided and handled.
            self.timers.consume(event.payload.timer_id, self.clock.now())
        for alternative in reaction.alternatives:
            kind, name, argument = _parse(alternative)
            if kind == "defer":
                self._append(case, InternalEventType.EVENT_DEFERRED, event, recorded, "defer")
                self.inbox.mark_deferred(inbox_id, case.id, recorded.id)
                return DispatchResult(inbox_id, case.id, "defer", case.state)
            if kind == "ignore":
                self._ignore(case, event, recorded, inbox_id, "matrix")
                return DispatchResult(inbox_id, case.id, "ignore:matrix", case.state)
            if kind == "absorb":
                self._accepted(case, event, inbox_id, recorded)
                return DispatchResult(inbox_id, case.id, "absorb", case.state)
            if kind == "step":
                return self._step(case, event, recorded, inbox_id, name)
            moved = self._transition(case, event, recorded, name, argument)
            if moved is not None:
                self._accepted(case, event, inbox_id, recorded)
                final = self._advance(moved, recorded)
                label = f"escalate:{argument}" if name == "escalate" else f"transition:{name}"
                return DispatchResult(inbox_id, case.id, label, final.state)
        # Every alternative's guards failed. The matrix is meant to make this impossible;
        # if it happens, a person looks rather than the event vanishing.
        moved, _ = self._escalate(
            case, recorded, ReviewCode.INTERNAL_ERROR, f"no reaction applied: {reaction.raw}"
        )
        self._accepted(case, event, inbox_id, recorded)
        return DispatchResult(inbox_id, case.id, "escalate:internal_error", moved.state)

    def _transition(
        self, case: Case, event: InboundEvent, recorded: Event, name: str, argument: str
    ) -> Case | None:
        if name == "escalate":
            moved, _ = self._escalate(case, recorded, ReviewCode(argument), event.type.value)
            return moved
        if isinstance(event.payload, HumanReturnedCase):
            self._resolve_unknown_actions(case, event.payload)
        trigger = Trigger(name.upper())  # the table writes triggers in lower case
        review = (
            ReviewReason(code=ReviewCode.LATE_INBOUND, detail=event.type.value)
            if trigger is Trigger.LATE_INBOUND
            else None
        )
        try:
            moved, events = self.orchestrator.apply_trigger(
                case,
                trigger,
                Cause(kind=CauseKind.INPUT_EVENT, ref=str(recorded.id)),
                recorded.id,
                guards=self._guards(case, event),
                review_reason=review,
                actor=event.actor if event.actor.kind is ActorKind.HUMAN else None,
                commit=self._commit,
            )
        except IllegalTransition:  # includes GuardFailed: try the next alternative
            return None
        self._write(events)
        return moved

    def _step(
        self, case: Case, event: InboundEvent, recorded: Event, inbox_id: InboxId, name: str
    ) -> DispatchResult:
        if isinstance(event.payload, ApprovalDecided):
            payload = event.payload
            self.approvals.decide(
                payload.approval_id,
                ApprovalDecision(payload.decision),
                event.actor,
                self.clock.now(),
                payload.note,
            )
        if isinstance(event.payload, HumanTookCase):
            return self._hand_off(case, event, recorded, inbox_id, event.payload)
        default = self.orchestrator.step_for(case.state)
        # The state's own step in an automatic state (verify, apply_approval): just advance.
        # A waiting state never advances by itself, so its event step (reminder) is run here.
        if default is not None and default.name == name and case.state in AUTO_STATES:
            final = self._advance(case, recorded)  # the state's own step (verify, apply_approval)
        else:
            moved, events = self.orchestrator.run_named(
                case, name, recorded.id, commit=self._commit
            )
            self._write(events)
            final = self._advance(moved, recorded) if moved.state is not case.state else moved
        self._accepted(case, event, inbox_id, recorded)
        return DispatchResult(inbox_id, case.id, f"step:{name}", final.state)

    def _hand_off(
        self,
        case: Case,
        event: InboundEvent,
        recorded: Event,
        inbox_id: InboxId,
        choice: HumanTookCase,
    ) -> DispatchResult:
        """The hand_off step (T33, T36): a person takes the case.

        Without a notice, T33. With one, the spec drafts a handed_to_human_notice through the
        review queue (T34/T35); that template does not exist yet, so the notice is recorded
        as not sent (NOTICE_NOT_SENT) and the person still owns the case (T36) - they are
        told, in the trail, that the seller was not. BACKLOG: the notice template.
        """
        trigger = Trigger.HUMAN_TOOK_CASE
        if choice.notify_seller:
            trigger = Trigger.FOLLOW_UP_BLOCKED
            self._append(
                case,
                InternalEventType.NOTICE_NOT_SENT,
                event,
                recorded,
                "no handed_to_human_notice template yet; the seller was not told",
            )
        moved, events = self.orchestrator.apply_trigger(
            case,
            trigger,
            Cause(kind=CauseKind.INPUT_EVENT, ref=str(recorded.id)),
            recorded.id,
            guards=self._guards(case, event),
            actor=event.actor,
            commit=self._commit,
        )
        self._write(events)
        self._accepted(case, event, inbox_id, recorded)
        return DispatchResult(inbox_id, case.id, f"step:hand_off:{trigger.value}", moved.state)

    # -- guards an inbound event's transitions read (transitions.yaml `guards`) ----------------

    def _guards(self, case: Case, event: InboundEvent) -> dict[str, bool]:
        now = self.clock.now()
        guards: dict[str, bool] = {"actor_is_human": event.actor.kind is ActorKind.HUMAN}
        if case.pending_intent_id is not None:
            source = self.intents.get(case.pending_intent_id).source_kind
            guards["pending_source_proposal"] = source is IntentSource.PROPOSAL
            guards["pending_source_timer"] = source is IntentSource.TIMER
            guards["pending_source_follow_up"] = source is IntentSource.FOLLOW_UP
        window = timedelta(days=self.config.timers.reopen_window_days)
        within = case.closed_at is not None and now < case.closed_at + window
        cap_ok = case.reopen_count < self.config.cases.max_agent_reopens
        not_superseded = case.superseded_by_case_id is None
        guards |= {
            "within_reopen_window": within,
            "reopen_cap_ok": cap_ok,
            "not_superseded": not_superseded,
            "reopen_guards_fail": not (within and cap_ok) and not_superseded,
            "no_verified_credit": not self.actions.has_verified_credit(case.id),
            "unknown_actions_resolved": not any(
                a.status in (ActionStatus.PLANNED, ActionStatus.SENT, ActionStatus.UNKNOWN)
                for a in self.actions.for_case(case.id)
            ),
        }
        if (
            isinstance(event.payload, TimerFired)
            and event.payload.kind is TimerKind.SELLER_DECISION
        ):
            anchor = self.timers.get(event.payload.timer_id).anchor_at
            guards["no_reply_since_anchor"] = self.messages.inbound_after(case.id, anchor) == 0
        if isinstance(event.payload, HumanTookCase):
            guards["notify_seller"] = event.payload.notify_seller
            guards["no_notify_seller"] = not event.payload.notify_seller
        return guards

    def _resolve_unknown_actions(self, case: Case, payload: HumanReturnedCase) -> None:
        """A person says what happened to each UNKNOWN action before the case returns (T37)."""
        mine = {a.id: a for a in self.actions.for_case(case.id)}
        for resolution in payload.unknown_action_resolutions:
            action = mine.get(resolution.action_id)
            if action is None or action.status is not ActionStatus.UNKNOWN:
                continue  # not this case's, or not unknown: the guard will say so
            done = resolution.resolution == "confirmed_done"
            self.actions.set_status(
                action.id,
                ActionStatus.VERIFIED if done else ActionStatus.FAILED,
                self.clock.now(),
                last_error_code=None if done else "confirmed_not_done_by_human",
                last_error_detail=resolution.evidence_note[:300],
            )

    # -- bookkeeping ------------------------------------------------------------------------------

    def _accepted(
        self, case: Case, event: InboundEvent, inbox_id: InboxId, recorded: Event
    ) -> None:
        """The event was handled (anything but defer): record what it brings, mark it done."""
        if isinstance(event.payload, SellerReplyReceived):
            self.messages.record_inbound(self.ids.new_id("msg"), case.id, inbox_id, event.payload)
        self.inbox.mark_processed(inbox_id, case.id, recorded.id)

    def _ignore(
        self, case: Case, event: InboundEvent, recorded: Event, inbox_id: InboxId, why: str
    ) -> None:
        self._append(case, InternalEventType.EVENT_IGNORED, event, recorded, f"ignore:{why}")
        if isinstance(event.payload, TimerFired):
            self.timers.consume(event.payload.timer_id, self.clock.now())
        self.inbox.mark_processed(inbox_id, case.id, recorded.id)

    def _escalate(
        self, case: Case, recorded: Event, code: ReviewCode, detail: str
    ) -> tuple[Case, tuple[Event, ...]]:
        moved, events = self.orchestrator.escalate(
            case, ReviewReason(code=code, detail=detail[:200]), recorded.id, commit=self._commit
        )
        self._write(events)
        return moved, events

    def _advance(self, case: Case, recorded: Event) -> Case:
        result = self.orchestrator.advance(case, recorded.id, commit=self._commit)
        self._write(result.events)
        return result.case

    def _redeliver(self, case_id: CaseId | None) -> None:
        """Deferred events whose reaction is no longer `defer` (orchestrator.md 3.4)."""
        if case_id is None:
            return
        for _ in range(20):  # each pass may move the case; a bound, not a loop forever
            case = self.cases.get(case_id)
            held_types: set[str] = set()
            delivered = False
            for row in self.inbox.deferred_for_case(case_id):
                key = reaction_key(parse_inbound(str(row["event_json"])))
                if key in held_types:
                    continue  # a reply never overtakes an earlier reply
                if self.machine.react(case.state, key).primary == "defer":
                    held_types.add(key)
                    continue
                self.dispatch(InboxId(str(row["id"])))
                delivered = True
                break
            if not delivered:
                return

    def _input_event(
        self, row: Mapping[str, object], event: InboundEvent, case: Case, inbox_id: InboxId
    ) -> Event:
        """The input event, appended once ever (a redelivery reuses it)."""
        if row.get("event_id"):
            return self.events.get(EventId(str(row["event_id"])))
        return self.events.append(
            Event(
                id=EventId(f"evt_{str(inbox_id).removeprefix('inb_')}"),
                case_id=case.id,
                type=event.type,
                occurred_at=event.occurred_at,
                recorded_at=self.clock.now(),
                actor=event.actor,
                cause=Cause(kind=CauseKind.INPUT_EVENT, ref=str(inbox_id)),
                correlation_id=event.correlation_id or case.correlation_id,
                dedupe_key=event.dedupe_key,
                # No outside text in the audit trail: the inbox holds the event (I5).
                payload=EventPayload(),
            )
        )

    def _append(
        self,
        case: Case,
        kind: InternalEventType,
        event: InboundEvent,
        recorded: Event,
        reaction: str,
    ) -> None:
        self.events.append(
            Event(
                id=EventId(self.ids.new_id("evt")),
                case_id=case.id,
                type=kind,
                occurred_at=self.clock.now(),
                recorded_at=self.clock.now(),
                actor=_system(),
                cause=Cause(kind=CauseKind.INPUT_EVENT, ref=str(recorded.id)),
                correlation_id=CorrelationId(str(case.correlation_id)),
                causation_id=recorded.id,
                payload=EventRejectedPayload(
                    event_type=reaction_key(event), state=case.state, reaction=reaction
                ),
            )
        )

    def _write(self, events: Sequence[Event]) -> None:
        for item in events:
            self.events.append(item)

    def _commit(self, before: Case, after: Case) -> Case:
        return self.cases.save(after, before.version)


def _parse(alternative: str) -> tuple[str, str, str]:
    """'transition:escalate(ops_timeout)' -> ('transition', 'escalate', 'ops_timeout')."""
    kind, _, rest = alternative.partition(":")
    name, _, argument = rest.partition("(")
    return kind.strip(), name.strip(), argument.rstrip(")").strip()


def _case_id(row: Mapping[str, object]) -> CaseId | None:
    value = row.get("case_id")
    return CaseId(str(value)) if value else None


def _system() -> Actor:
    from dispute_agent.kernel.statemachine.machine import SYSTEM

    return SYSTEM


__all__ = ["DispatchResult", "Dispatcher"]
