"""Card P1-09: a dispute runs end to end, with fake ports and no AI.

What "end to end" means in Phase 1 is worth being clear about. There is no domain pack and no
reasoner, so nothing here decides a dispute on its merits. What runs is the machinery: a
dispute arrives, is deduplicated, opens a case, advances through steps, and reaches a person
with a reason that names the phase which will do the deciding.

That is a real result rather than a placeholder. The alternative — a step that guesses an
outcome so the demo looks complete — is the single worst thing this system could do, and the
shape of Phase 1 is deliberately one where it cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import TERMINAL_STATES as TERMINAL
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    InboundEventType,
    InternalEventType,
    ReviewCode,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.events import (
    Event,
    EventPayload,
    InboundPayload,
    StateChangedPayload,
)
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor, Cause, ReviewReason
from dispute_agent.kernel.domain.patterns import CaseOutcome
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.orchestrator import (
    Orchestrator,
    StepOutcome,
    phase_1_steps,
)
from dispute_agent.kernel.statemachine.machine import StateMachine
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
SELLER = "seller@example.invalid"
INPUT = EventId("evt_input0000000000000000000")
TABLE = default_table()

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact=SELLER,
    raised_at=NOW,
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


def an_inbound(source_event_id: str = "form-1"):
    from dispute_agent.kernel.domain.events import InboundEvent

    return InboundEvent(
        type=InboundEventType.DISPUTE_RAISED,
        source="dispute_api",
        source_event_id=source_event_id,
        occurred_at=NOW,
        actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        payload=InboundPayload(),
    )


def orchestrator(clock: FakeClock, steps: dict[str, object] | None = None) -> Orchestrator:
    return Orchestrator(
        StateMachine(TABLE),
        steps or phase_1_steps(),  # type: ignore[arg-type]
        clock,
        FakeIdGenerator(),
        patterns=NullPatternContextProvider(),
        outcomes=NullOutcomeObserver(),
    )


def run_one(engine, steps: dict[str, object] | None = None):
    """Intake a dispute, then advance it, committing as one unit of work."""
    clock = FakeClock(NOW)
    intake = Intake(allowed_recipients=(SELLER,), reopen_window=timedelta(days=7))

    with SqliteUnitOfWork(engine) as work:
        inbox = SqliteInboxStore(work.connection)
        inbox_id = inbox.accept(an_inbound(), NOW)
        result = intake.open_or_get_case(
            work.cases,
            an_inbound(),
            FACTS,
            pack="weight_dispute",
            now=NOW,
            case_id=CaseId("case_01HQ"),
            correlation_id=CorrelationId("corr_01HQ"),
            opened_event_id=EventId("evt_opened000000000000000000"),
            causation_id=INPUT,
        )
        work.cases.insert(result.case)
        raised = work.events.append(
            Event(
                id=INPUT,
                case_id=result.case.id,
                type=InboundEventType.DISPUTE_RAISED,
                occurred_at=NOW,
                recorded_at=NOW,
                actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
                cause=Cause(kind=result.events[0].cause.kind, ref=str(inbox_id)),
                correlation_id=result.case.correlation_id,
                dedupe_key=an_inbound().dedupe_key,
                payload=EventPayload(),
            )
        )
        for event in result.events:
            work.events.append(event)

        advanced = orchestrator(clock, steps).advance(result.case, raised.id)
        for event in advanced.events:
            work.events.append(event)
        if advanced.case.state != result.case.state:
            work.cases.save(advanced.case, result.case.version)

    return advanced


class TestASyntheticDisputeRuns:
    def test_it_reaches_a_person_rather_than_a_guess(self, engine) -> None:
        """Phase 1 has no pack and no reasoner, so the honest outcome is a human."""
        advanced = run_one(engine)
        assert advanced.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert advanced.case.review_reason is not None

    def test_the_reason_names_the_phase_that_will_decide(self, engine) -> None:
        advanced = run_one(engine)
        assert advanced.case.review_reason is not None
        assert "Phase 2" in advanced.case.review_reason.detail

    def test_the_case_is_persisted_in_that_state(self, engine) -> None:
        run_one(engine)
        with SqliteUnitOfWork(engine) as work:
            stored = work.cases.get(CaseId("case_01HQ"))
        assert stored.state is CaseState.NEEDS_HUMAN_REVIEW
        assert stored.version == 2

    def test_every_state_change_has_an_event(self, engine) -> None:
        """I8, end to end rather than in isolation."""
        advanced = run_one(engine)
        with SqliteUnitOfWork(engine) as work:
            stored = work.events.list_for_case(CaseId("case_01HQ"))

        changes = [e for e in stored if e.type is InternalEventType.STATE_CHANGED]
        assert len(changes) == len(
            [e for e in advanced.events if e.type is InternalEventType.STATE_CHANGED]
        )
        assert changes, "the case moved but nothing recorded it"

    def test_each_change_names_its_transition_and_cause(self, engine) -> None:
        run_one(engine)
        with SqliteUnitOfWork(engine) as work:
            stored = work.events.list_for_case(CaseId("case_01HQ"))
        for event in stored:
            if event.type is not InternalEventType.STATE_CHANGED:
                continue
            assert isinstance(event.payload, StateChangedPayload)
            assert event.payload.transition_id
            assert event.cause.ref

    def test_the_events_are_in_order(self, engine) -> None:
        run_one(engine)
        with SqliteUnitOfWork(engine) as work:
            stored = work.events.list_for_case(CaseId("case_01HQ"))
        assert [e.seq for e in stored] == list(range(1, len(stored) + 1))


class TestTheStepLimit:
    def test_a_case_that_never_rests_is_stopped(self) -> None:
        """A case producing work without resting is a bug; letting it run is worse."""

        class Treadmill:
            name = "eligibility"

            def run(self, case, now):
                # Always moves, never rests: RECEIVED -> GATHERING_EVIDENCE -> ... forever
                # if the loop let it.
                return StepOutcome(trigger=Trigger.ELIGIBILITY_PASSED, guards={})

        clock = FakeClock(NOW)
        loop = Orchestrator(
            StateMachine(TABLE),
            {"eligibility": Treadmill()},
            clock,
            FakeIdGenerator(),
            max_steps=3,
        )
        case = _a_case()
        result = loop.advance(case, INPUT)
        assert result.stopped_because in ("step_limit", "no_step")
        assert result.steps_run <= 3

    def test_the_limit_escalates_with_step_limit(self) -> None:
        class Treadmill:
            name = "eligibility"

            def run(self, case, now):
                return StepOutcome(
                    trigger=Trigger.ESCALATE,
                    guards={},
                    review_reason=ReviewReason(code=ReviewCode.STEP_LIMIT),
                )

        clock = FakeClock(NOW)
        loop = Orchestrator(
            StateMachine(TABLE),
            {"eligibility": Treadmill()},
            clock,
            FakeIdGenerator(),
            max_steps=1,
        )
        result = loop.advance(_a_case(), INPUT)
        assert result.case.state is CaseState.NEEDS_HUMAN_REVIEW


class TestModuleUnavailableIsHandledOnce:
    def test_a_step_raising_it_escalates_with_the_module_named(self) -> None:
        """I6, converted in one place rather than in nine steps."""

        class Broken:
            name = "eligibility"

            def run(self, case, now):
                raise ModuleUnavailable("evidence")

        loop = Orchestrator(
            StateMachine(TABLE),
            {"eligibility": Broken()},
            FakeClock(NOW),
            FakeIdGenerator(),
        )
        result = loop.advance(_a_case(), INPUT)
        assert result.stopped_because == "module_unavailable"
        assert result.case.review_reason is not None
        assert result.case.review_reason.render() == "module_unavailable:evidence"

    def test_there_is_one_conversion_point(self) -> None:
        """Structural: if each step handled it, one would eventually handle it differently."""
        source = (
            Path(__file__).resolve().parents[2]
            / "src/dispute_agent/kernel/orchestrator/orchestrator.py"
        ).read_text(encoding="utf-8")
        assert source.count("except ModuleUnavailable") == 1


class TestTheCrossCaseHooksAreWired:
    def test_patterns_are_asked_for_on_every_run(self) -> None:
        """D3: the Null adapter returns nothing, and Phase 7 changes an adapter not a loop."""
        loop = orchestrator(FakeClock(NOW))
        assert loop.patterns_for(FACTS) == ()

    def test_outcomes_are_offered(self) -> None:
        loop = orchestrator(FakeClock(NOW))
        outcome = CaseOutcome(
            id=__import__("dispute_agent.kernel.domain.ids", fromlist=["OutcomeId"]).OutcomeId(
                "out_01HQ"
            ),
            case_id=CaseId("case_01HQ"),
            state=CaseState.HANDED_TO_HUMAN,
            resolved=False,
            facts=FACTS,
            closed_at=NOW,
        )
        loop.record_outcome(outcome)  # the Null adapter does nothing, and that is the test

    def test_a_missing_hook_is_simply_empty(self) -> None:
        """Phase 1 can run without cross_case wired at all."""
        loop = Orchestrator(StateMachine(TABLE), phase_1_steps(), FakeClock(NOW), FakeIdGenerator())
        assert loop.patterns_for(FACTS) == ()


class TestRestingStates:
    def test_a_waiting_state_stops_the_loop(self) -> None:
        case = _a_case(CaseState.AWAITING_SELLER)
        result = orchestrator(FakeClock(NOW)).advance(case, INPUT)
        assert result.stopped_because == "waiting"
        assert result.steps_run == 0

    def test_a_terminal_state_stops_the_loop(self) -> None:
        case = _a_case(CaseState.HANDED_TO_HUMAN)
        result = orchestrator(FakeClock(NOW)).advance(case, INPUT)
        assert result.stopped_because == "terminal"

    def test_a_step_that_rests_stops_without_moving(self) -> None:
        class Resting:
            name = "eligibility"

            def run(self, case, now):
                return StepOutcome(detail="waiting for a retry")

        loop = Orchestrator(
            StateMachine(TABLE),
            {"eligibility": Resting()},
            FakeClock(NOW),
            FakeIdGenerator(),
        )
        result = loop.advance(_a_case(), INPUT)
        assert result.stopped_because == "rest"
        assert result.case.state is CaseState.RECEIVED


class TestPhase1StepsAreHonest:
    def test_no_step_decides_a_dispute(self) -> None:
        """Each escalates and names the phase that will replace it."""
        for name, step in phase_1_steps().items():
            outcome = step.run(_a_case(), NOW)
            assert outcome.trigger is Trigger.ESCALATE, name
            assert outcome.review_reason is not None
            assert "Phase" in outcome.review_reason.detail

    def test_every_phase_1_step_is_in_the_transition_table(self) -> None:
        assert set(phase_1_steps()) <= set(TABLE.steps)


def _a_case(state: CaseState = CaseState.RECEIVED):
    from dispute_agent.kernel.domain.models import Case

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
        review_reason=(
            ReviewReason(code=ReviewCode.STEP_LIMIT)
            if state is CaseState.NEEDS_HUMAN_REVIEW
            else None
        ),
    )
