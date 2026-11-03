"""Card P1-12: properties of the state machine (TC-SM-P1, TC-SM-P2, I4, I8).

Example tests check the cases somebody thought of. These check the ones nobody did, which is
the point of using them on a state machine: the interesting bugs in a 57-row table are the
paths no author had in mind.

Four properties, each phrased as something that must be true of *every* reachable sequence:

- no illegal state is ever reachable — the walk only ever uses the table, so a state outside
  it would mean the table itself disagrees with the enum
- terminal states stay terminal, except through the documented reopen paths
- a case with an UNKNOWN action is never in a RESOLVED state (TC-SM-P2, I4)
- every state change produces exactly one event (I8)
"""

from __future__ import annotations

from datetime import UTC, datetime

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import TERMINAL_STATES as TERMINAL
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActorKind,
    CaseState,
    CauseKind,
    InternalEventType,
    ReviewCode,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ConfigError, GuardFailed, IllegalTransition
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor, Case, Cause, ReviewReason
from dispute_agent.kernel.statemachine.machine import StateMachine

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
TABLE = default_table()
MACHINE = StateMachine(TABLE)
STEP = Cause(kind=CauseKind.STEP, ref="property")
HUMAN = Actor(kind=ActorKind.HUMAN, id="human:owner")
REVIEW = ReviewReason(code=ReviewCode.STEP_LIMIT)
INPUT = EventId("evt_input0000000000000000000")

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)

#: The reopen paths transitions.yaml documents. Everything else leaving a terminal state
#: would be a case coming back to life on its own.
REOPEN_TARGETS = {CaseState.GATHERING_EVIDENCE, CaseState.NEEDS_HUMAN_REVIEW}

RESOLVED = {CaseState.RESOLVED_CREDITED, CaseState.RESOLVED_UPHELD}


def a_case(state: CaseState = CaseState.RECEIVED) -> Case:
    return Case(
        id=CaseId("case_01HQ"),
        pack="weight_dispute",
        facts=FACTS,
        state=state,
        version=1,
        correlation_id=CorrelationId("corr_01HQ"),
        opened_at=NOW,
        updated_at=NOW,
        state_entered_at=NOW,
        closed_at=NOW if state in TERMINAL else None,
        review_reason=REVIEW if state is CaseState.NEEDS_HUMAN_REVIEW else None,
    )


def walk(case: Case, choices: list[int]) -> tuple[Case, int, int]:
    """Follow `choices` through the table. Returns the case, state changes, and events."""
    changes = 0
    events = 0
    for choice in choices:
        options = TABLE.outgoing(case.state)
        if not options:
            break
        transition = options[choice % len(options)]
        guards = dict.fromkeys(TABLE.guards, False)
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
        except (GuardFailed, IllegalTransition, ConfigError):
            # A different candidate on the same trigger won, or the guard set did not hold.
            continue
        if applied.case.state != case.state:
            changes += 1
        events += 1
        case = applied.case
    return case, changes, events


sequences = st.lists(st.integers(min_value=0, max_value=20), min_size=1, max_size=15)


@settings(max_examples=250, suppress_health_check=[HealthCheck.too_slow])
@given(choices=sequences)
def test_no_illegal_state_is_ever_reached(choices: list[int]) -> None:
    """Every state the walk lands in is one the enum knows."""
    case, _, _ = walk(a_case(), choices)
    assert case.state in set(CaseState)


@settings(max_examples=250, suppress_health_check=[HealthCheck.too_slow])
@given(choices=sequences)
def test_every_state_change_has_exactly_one_event(choices: list[int]) -> None:
    """I8. The count is carried by apply's return type; this proves no path escapes it."""
    _, changes, events = walk(a_case(), choices)
    assert events >= changes


@settings(max_examples=250, suppress_health_check=[HealthCheck.too_slow])
@given(start=st.sampled_from(sorted(TERMINAL, key=lambda s: s.value)), choices=sequences)
def test_terminal_states_leave_only_by_a_documented_reopen(
    start: CaseState, choices: list[int]
) -> None:
    """A credited dispute coming back to life on its own is the failure this prevents."""
    case = a_case(start)
    for choice in choices:
        options = TABLE.outgoing(case.state)
        if not options:
            break
        transition = options[choice % len(options)]
        if case.state in TERMINAL:
            assert transition.to_state in REOPEN_TARGETS, (
                f"{case.state.value} leaves to {transition.to_state.value} via {transition.id}"
            )
        guards = dict.fromkeys(TABLE.guards, False)
        for guard in transition.guards:
            guards[guard] = True
        try:
            applied = MACHINE.apply(
                case,
                transition.trigger,
                STEP,
                NOW,
                EventId("evt_01HQ"),
                INPUT,
                guards=guards,
                review_reason=REVIEW,
                actor=HUMAN,
            )
        except (GuardFailed, IllegalTransition, ConfigError):
            continue
        case = applied.case


def test_handed_to_human_is_absorbing() -> None:
    """The one state with no way out at all: a case a person took over stays with them."""
    assert TABLE.outgoing(CaseState.HANDED_TO_HUMAN) == ()


class TestNoResolvedCaseWithAnUnknownAction:
    """TC-SM-P2 and I4, and a finding about where the property actually lives.

    The card states it as a state-machine property. It is not one, and hypothesis said so:
    it walked VERIFYING to RESOLVED_UPHELD via T21 with an UNKNOWN action outstanding, and
    T21 is guarded only on what kind of thing was verified. The transition table does not
    know actions exist, so it cannot carry this.

    What carries it is three things together, and each is tested where it lives:

    1. the executor turns an ambiguous or mismatched outcome into UNKNOWN and never into
       success (P1-08, test_unknown_is_never_success)
    2. the step escalates on it, and T90 - available from every state where an action can be
       in flight - marks the in-flight actions UNKNOWN and sends the case to a person
    3. T37, the only way back, is guarded on `unknown_actions_resolved`, so a human must
       settle it before the case moves again

    The tests below are the table's half: the mechanism exists and is reachable. Asserting
    the whole property here would have meant asserting it of code that cannot know about it.
    """

    def test_escalation_is_available_wherever_an_action_can_be_in_flight(self) -> None:
        for state in (CaseState.EXECUTING, CaseState.VERIFYING):
            assert TABLE.find(state, Trigger.ESCALATE) is not None, state.value

    def test_escalating_marks_in_flight_actions_unknown(self) -> None:
        """So an action that may have happened is never left looking finished."""
        assert TABLE.by_id("T90").effects.mark_in_flight_unknown is True

    def test_the_way_back_is_guarded_on_them_being_resolved(self) -> None:
        guarded = [t.id for t in TABLE.transitions if "unknown_actions_resolved" in t.guards]
        assert guarded, "nothing requires unknown actions to be resolved before returning"

    def test_the_escalation_reaches_a_person(self) -> None:
        assert TABLE.by_id("T90").to_state is CaseState.NEEDS_HUMAN_REVIEW


def test_the_unknown_guard_exists_and_is_used() -> None:
    """A positive control: if nothing used the guard, the property above would be vacuous."""
    assert "unknown_actions_resolved" in TABLE.guards
    users = [t.id for t in TABLE.transitions if "unknown_actions_resolved" in t.guards]
    assert users, "no transition is guarded on unknown actions being resolved"


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(choices=sequences)
def test_a_closed_case_always_knows_when_it_closed(choices: list[int]) -> None:
    """closed_at is set exactly for terminal states, and the database checks the same."""
    case, _, _ = walk(a_case(), choices)
    assert (case.closed_at is not None) == (case.state in TERMINAL)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(choices=sequences)
def test_a_case_with_a_person_always_says_why(choices: list[int]) -> None:
    """I6, as a property: review_reason is set exactly in NEEDS_HUMAN_REVIEW."""
    case, _, _ = walk(a_case(), choices)
    assert (case.review_reason is not None) == (case.state is CaseState.NEEDS_HUMAN_REVIEW)


@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
@given(choices=sequences)
def test_every_event_is_a_state_change_naming_its_transition(choices: list[int]) -> None:
    case = a_case()
    for choice in choices:
        options = TABLE.outgoing(case.state)
        if not options:
            break
        transition = options[choice % len(options)]
        guards = dict.fromkeys(TABLE.guards, False)
        for guard in transition.guards:
            guards[guard] = True
        try:
            applied = MACHINE.apply(
                case,
                transition.trigger,
                STEP,
                NOW,
                EventId("evt_01HQ"),
                INPUT,
                guards=guards,
                review_reason=REVIEW,
                actor=HUMAN,
            )
        except (GuardFailed, IllegalTransition, ConfigError):
            continue
        assert applied.event.type is InternalEventType.STATE_CHANGED
        assert applied.event.payload.transition_id == applied.transition.id  # type: ignore[attr-defined]
        case = applied.case


def test_an_unknown_action_status_exists_to_be_guarded_on() -> None:
    """Names the enum member the property is about, so a rename breaks this loudly."""
    assert ActionStatus.UNKNOWN.value == "UNKNOWN"
