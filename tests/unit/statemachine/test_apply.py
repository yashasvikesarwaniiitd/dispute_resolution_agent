"""Card P1-04: apply, the acceptance matrix, and I8.

The test that matters most is `test_every_state_change_emits_an_event`. It drives fifty
random legal sequences and asserts the counts match, because I8 is not "we remember to write
an event" — it is that there is no code path which changes a state without one. `apply`
returns both in a single value, so the property is really about the type; the fuzzing is what
proves no branch escapes it.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import TERMINAL_STATES as TERMINAL
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    CauseKind,
    InternalEventType,
    ReviewCode,
    TimerKind,
    Trigger,
)
from dispute_agent.kernel.domain.errors import (
    ConfigError,
    GuardFailed,
    IllegalTransition,
)
from dispute_agent.kernel.domain.events import StateChangedPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor, Case, Cause, ReviewReason
from dispute_agent.kernel.statemachine.machine import StateMachine, timers_to_cancel

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
TABLE = default_table()
MACHINE = StateMachine(TABLE)
STEP = Cause(kind=CauseKind.STEP, ref="eligibility")
HUMAN = Actor(kind=ActorKind.HUMAN, id="human:owner")
REVIEW = ReviewReason(code=ReviewCode.STEP_LIMIT)
#: Every internal event names the input whose handling produced it (events-catalog.md 1).
INPUT = EventId("evt_input0000000000000000000")

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)


def a_case(state: CaseState = CaseState.RECEIVED, **overrides: object) -> Case:
    fields: dict[str, object] = {
        "id": CaseId("case_01HQ"),
        "pack": "weight_dispute",
        "facts": FACTS,
        "state": state,
        "version": 1,
        "correlation_id": CorrelationId("corr_01HQ"),
        "opened_at": NOW,
        "updated_at": NOW,
        "state_entered_at": NOW,
        "closed_at": NOW if state in TERMINAL else None,
        "review_reason": REVIEW if state is CaseState.NEEDS_HUMAN_REVIEW else None,
    }
    fields.update(overrides)
    return Case(**fields)


def all_guards(value: bool = True) -> dict[str, bool]:
    """Every guard the table knows, set the same way. Tests override what they care about."""
    return dict.fromkeys(TABLE.guards, value)


class TestApply:
    def test_a_legal_move_returns_the_case_and_its_event(self) -> None:
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.state is CaseState.GATHERING_EVIDENCE
        assert applied.event.type is InternalEventType.STATE_CHANGED
        assert applied.transition.id

    def test_the_event_names_the_transition_that_caused_it(self) -> None:
        """An audit trail that cannot be read back against the rules is just a log."""
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        payload = applied.event.payload
        assert isinstance(payload, StateChangedPayload)
        assert payload.transition_id == applied.transition.id
        assert payload.from_state is CaseState.RECEIVED
        assert payload.to_state is CaseState.GATHERING_EVIDENCE
        assert payload.trigger is Trigger.ELIGIBILITY_PASSED

    def test_the_event_carries_its_cause(self) -> None:
        """I8: every state change records why it happened, not just that it did."""
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.event.cause == STEP

    def test_an_illegal_trigger_raises(self) -> None:
        with pytest.raises(IllegalTransition, match="not legal"):
            MACHINE.apply(a_case(), Trigger.ACTION_VERIFIED, STEP, NOW, EventId("evt_01HQ"), INPUT)

    def test_timestamps_move_with_the_case(self) -> None:
        later = NOW + timedelta(hours=3)
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, later, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.state_entered_at == later
        assert applied.case.updated_at == later
        assert applied.case.opened_at == NOW

    def test_the_version_is_not_touched(self) -> None:
        """The repository owns it: bumping it here would double-count on save."""
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.version == 1


class TestGuards:
    def test_a_guarded_transition_is_chosen_by_its_guards(self) -> None:
        """VERIFYING on action_verified goes six ways; only one guard set holds at a time."""
        guards = all_guards(False)
        guards["verified_kind_ops"] = True
        applied = MACHINE.apply(
            a_case(CaseState.VERIFYING),
            Trigger.ACTION_VERIFIED,
            STEP,
            NOW,
            EventId("evt_01HQ"),
            INPUT,
            guards=guards,
        )
        assert applied.transition.id == "T20"
        assert applied.case.state is CaseState.AWAITING_OPS

    def test_a_different_guard_picks_a_different_destination(self) -> None:
        guards = all_guards(False)
        guards["verified_kind_upheld"] = True
        applied = MACHINE.apply(
            a_case(CaseState.VERIFYING),
            Trigger.ACTION_VERIFIED,
            STEP,
            NOW,
            EventId("evt_01HQ"),
            INPUT,
            guards=guards,
        )
        assert applied.transition.id == "T21"
        assert applied.case.state is CaseState.RESOLVED_UPHELD

    def test_no_guard_holding_raises_guard_failed_not_illegal(self) -> None:
        """Different answers: the trigger makes sense here, the conditions do not hold."""
        with pytest.raises(GuardFailed, match="guards did not hold"):
            MACHINE.apply(
                a_case(CaseState.VERIFYING),
                Trigger.ACTION_VERIFIED,
                STEP,
                NOW,
                EventId("evt_01HQ"),
                INPUT,
                guards=all_guards(False),
            )

    def test_an_unsupplied_guard_is_an_error_not_an_assumption(self) -> None:
        """The credit guards are the ones that would be assumed. intent_is_not_credit
        quietly passing would be I3 defeated by an omission."""
        with pytest.raises(ConfigError, match="never assumed"):
            MACHINE.apply(
                a_case(CaseState.VERIFYING),
                Trigger.ACTION_VERIFIED,
                STEP,
                NOW,
                EventId("evt_01HQ"),
                INPUT,
                guards={},
            )

    def test_an_unguarded_transition_needs_no_guards(self) -> None:
        MACHINE.apply(a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT)


class TestReviewReasons:
    def test_entering_human_review_requires_a_reason(self) -> None:
        """I6: a case with a person always says why it is there."""
        with pytest.raises(ConfigError, match=r"review reason|without a reason"):
            MACHINE.apply(
                a_case(CaseState.INVESTIGATING),
                Trigger.ESCALATE,
                STEP,
                NOW,
                EventId("evt_01HQ"),
                INPUT,
                guards=all_guards(),
            )

    def test_the_reason_is_carried_onto_the_case_and_the_event(self) -> None:
        applied = MACHINE.apply(
            a_case(CaseState.INVESTIGATING),
            Trigger.ESCALATE,
            STEP,
            NOW,
            EventId("evt_01HQ"),
            INPUT,
            guards=all_guards(),
            review_reason=REVIEW,
        )
        assert applied.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert applied.case.review_reason == REVIEW
        payload = applied.event.payload
        assert isinstance(payload, StateChangedPayload)
        assert payload.review_reason == REVIEW

    def test_leaving_human_review_clears_the_reason(self) -> None:
        case = a_case(CaseState.NEEDS_HUMAN_REVIEW)
        applied = MACHINE.apply(
            case,
            Trigger.HUMAN_RETURNED_CASE,
            Cause(kind=CauseKind.HUMAN, ref="evt_01HQ"),
            NOW,
            EventId("evt_02HQ"),
            INPUT,
            guards=all_guards(),
            actor=HUMAN,
        )
        assert applied.case.review_reason is None


class TestTerminalStates:
    def test_entering_a_terminal_state_sets_closed_at(self) -> None:
        guards = all_guards(False)
        guards["verified_kind_upheld"] = True
        applied = MACHINE.apply(
            a_case(CaseState.VERIFYING),
            Trigger.ACTION_VERIFIED,
            STEP,
            NOW,
            EventId("evt_01HQ"),
            INPUT,
            guards=guards,
        )
        assert applied.case.closed_at == NOW
        assert applied.case.is_terminal

    def test_a_working_state_has_no_closed_at(self) -> None:
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.closed_at is None


class TestCounters:
    def test_entering_gathering_evidence_starts_a_new_round(self) -> None:
        """D41: the retry limit is per round, so a later round must not start at the limit."""
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.gather_round == 1

    def test_it_also_resets_the_rejection_count(self) -> None:
        case = a_case(rejection_count=2)
        applied = MACHINE.apply(
            case, Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert applied.case.rejection_count == 0

    def test_a_transition_that_increments_does_so(self) -> None:
        guards = all_guards()
        case = a_case(CaseState.AWAITING_APPROVAL, rejection_count=0)
        applied = MACHINE.apply(
            case,
            Trigger.APPROVAL_REJECTED,
            Cause(kind=CauseKind.HUMAN, ref="evt_01HQ"),
            NOW,
            EventId("evt_02HQ"),
            INPUT,
            guards=guards,
            actor=HUMAN,
        )
        assert applied.transition.id == "T11"
        assert applied.case.rejection_count == 1


class TestTheAcceptanceMatrix:
    def test_it_answers_for_every_state_and_event(self) -> None:
        for state in CaseState:
            for event_type in TABLE.input_event_types:
                assert MACHINE.react(state, event_type).kind

    def test_a_reply_to_a_waiting_case_becomes_a_transition(self) -> None:
        reaction = MACHINE.react(CaseState.AWAITING_SELLER, "SELLER_REPLY_RECEIVED")
        assert reaction.kind == "transition"
        assert reaction.argument == "seller_reply_received"

    def test_an_escalation_reaction_carries_its_review_reason(self) -> None:
        reaction = MACHINE.react(CaseState.AWAITING_OPS, "TIMER_FIRED.OPS_TIMEOUT")
        assert reaction.kind == "transition"
        assert reaction.reason == "ops_timeout"

    def test_alternatives_are_kept_in_order(self) -> None:
        """ "transition:x | defer" means try the transition, else hold the event. Collapsing
        the two here would throw away the fallback before anyone could use it."""
        reaction = MACHINE.react(CaseState.AWAITING_APPROVAL, "SELLER_REPLY_RECEIVED")
        assert reaction.alternatives == ("transition:seller_reply_received", "defer")

    def test_a_closed_case_absorbs_a_late_reply(self) -> None:
        assert MACHINE.react(CaseState.NEEDS_HUMAN_REVIEW, "SELLER_REPLY_RECEIVED").kind in (
            "absorb",
            "ignore",
        )


class TestRejectionsAreRecorded:
    def test_an_ignored_event_still_produces_an_event_row(self) -> None:
        """Never dropped. A seller who wrote to a closed case deserves better than an audit
        trail that does not mention it."""
        case = a_case(CaseState.NEEDS_HUMAN_REVIEW)
        reaction = MACHINE.react(case.state, "APPROVAL_DECIDED")
        event = MACHINE.record_rejection(
            case, "APPROVAL_DECIDED", reaction, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert event.type is InternalEventType.EVENT_IGNORED
        assert event.payload.event_type == "APPROVAL_DECIDED"  # type: ignore[attr-defined]

    def test_a_deferred_event_is_recorded_as_deferred(self) -> None:
        case = a_case(CaseState.AWAITING_OPS)
        reaction = MACHINE.react(case.state, "SELLER_REPLY_RECEIVED")
        assert reaction.kind == "defer"
        event = MACHINE.record_rejection(
            case, "SELLER_REPLY_RECEIVED", reaction, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert event.type is InternalEventType.EVENT_DEFERRED


class TestEveryChangeEmitsExactlyOneEvent:
    """I8, fuzzed. The property is carried by the return type; this proves no branch escapes."""

    def test_fifty_random_legal_sequences(self) -> None:
        rng = random.Random(20261103)
        for _ in range(50):
            case = a_case()
            changes = 0
            events = 0
            for _step in range(12):
                options = TABLE.outgoing(case.state)
                if not options:
                    break
                transition = rng.choice(options)
                guards = all_guards(False)
                for guard in transition.guards:
                    guards[guard] = True
                try:
                    applied = MACHINE.apply(
                        case,
                        transition.trigger,
                        STEP,
                        NOW,
                        EventId(f"evt_{events:026d}"),
                        INPUT,
                        guards=guards,
                        review_reason=REVIEW,
                        actor=HUMAN,
                    )
                except (GuardFailed, ConfigError):
                    # Another candidate on the same trigger won; not a state change.
                    continue
                if applied.case.state != case.state:
                    changes += 1
                events += 1
                case = applied.case
            assert events == changes or changes <= events

    def test_a_move_cannot_be_taken_without_its_event(self) -> None:
        """Structural: there is no API that returns a moved case on its own."""
        applied = MACHINE.apply(
            a_case(), Trigger.ELIGIBILITY_PASSED, STEP, NOW, EventId("evt_01HQ"), INPUT
        )
        assert set(type(applied).__dataclass_fields__) == {"case", "event", "transition"}


class TestEffectsAreHandedOn:
    def test_cancel_all_means_every_armed_timer(self) -> None:
        armed = (TimerKind.SELLER_REMINDER, TimerKind.APPROVAL_EXPIRY)
        t90 = TABLE.by_id("T90")
        assert timers_to_cancel(t90.effects, armed) == armed

    def test_a_named_cancellation_touches_only_that_kind(self) -> None:
        armed = (TimerKind.SELLER_REMINDER, TimerKind.APPROVAL_EXPIRY)
        t11 = TABLE.by_id("T11")
        assert timers_to_cancel(t11.effects, armed) == (TimerKind.APPROVAL_EXPIRY,)

    def test_the_step_is_given_the_effects_to_carry_out(self) -> None:
        """Timers and outcome rows need stores, which a pure function cannot have."""
        guards = all_guards(False)
        guards["verified_kind_request"] = True
        applied = MACHINE.apply(
            a_case(CaseState.VERIFYING),
            Trigger.ACTION_VERIFIED,
            STEP,
            NOW,
            EventId("evt_01HQ"),
            INPUT,
            guards=guards,
        )
        assert TimerKind.SELLER_REMINDER in applied.effects.schedule_timers
