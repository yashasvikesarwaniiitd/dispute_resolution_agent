"""Card P1-10: a timer survives the process that armed it.

This is the card's reason to exist, so the test is written to actually prove it: arm a timer,
**throw away every object**, open the database again as a new process would, move a fake
clock past the due time, and ask what is due.

If timers lived in memory — a sleeping task, a scheduler holding deadlines — this test could
not be written, and the failure it guards against would only ever appear as a case that quietly
stopped moving after a deploy.
"""

from __future__ import annotations

import gc
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.timer_store import SqliteTimerStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import Timer
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import CaseState, TimerKind
from dispute_agent.kernel.domain.errors import DomainValidationError, NotFound
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, TimerId
from dispute_agent.kernel.domain.models import Case

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)


def a_case() -> Case:
    return Case(
        id=CaseId("case_01HQ"),
        pack="weight_dispute",
        facts=FACTS,
        state=CaseState.RECEIVED,
        version=1,
        correlation_id=CorrelationId("corr_01HQ"),
        opened_at=NOW,
        updated_at=NOW,
        state_entered_at=NOW,
    )


def a_timer(kind: TimerKind = TimerKind.SELLER_REMINDER, **overrides: object) -> Timer:
    fields: dict[str, object] = {
        "id": TimerId("tmr_01HQ"),
        "case_id": CaseId("case_01HQ"),
        "kind": kind,
        "due_at": NOW + timedelta(hours=48),
        "anchor_at": NOW,
        "armed_in_state": CaseState.AWAITING_SELLER,
        "armed_at_version": 1,
        "correlation_id": CorrelationId("corr_01HQ"),
        "created_at": NOW,
        "gather_round": 1 if kind is TimerKind.EVIDENCE_RETRY else None,
    }
    fields.update(overrides)
    return Timer(**fields)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(path)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    with SqliteUnitOfWork(engine) as work:
        work.cases.insert(a_case())
    engine.dispose()
    return path


@pytest.mark.integration
class TestRestartResumes:
    def test_a_timer_armed_before_a_restart_still_fires(self, database: Path) -> None:
        """The card's named test. Arm, drop everything, reopen, tick past the due time."""
        first = create_sqlite_engine(database)
        with SqliteUnitOfWork(first) as work:
            SqliteTimerStore(work.connection).schedule(a_timer())

        # The process ends here: every object from it is dropped.
        first.dispose()
        del first
        gc.collect()

        second = create_sqlite_engine(database)
        clock = FakeClock(NOW)
        with SqliteUnitOfWork(second) as work:
            store = SqliteTimerStore(work.connection)
            assert store.due(clock.now()) == []

            clock.advance(timedelta(hours=49))
            due = store.due(clock.now())

        assert [t.id for t in due] == [TimerId("tmr_01HQ")]
        assert due[0].kind is TimerKind.SELLER_REMINDER

    def test_nothing_is_due_before_its_moment(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        clock = FakeClock(NOW)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            clock.advance(timedelta(hours=47))
            assert store.due(clock.now()) == []

    def test_a_cancelled_timer_never_becomes_due(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        clock = FakeClock(NOW)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.cancel(TimerId("tmr_01HQ"), NOW, "state_left")
            clock.advance(timedelta(hours=49))
            assert store.due(clock.now()) == []

    def test_firing_twice_is_refused(self, database: Path) -> None:
        """Two schedulers racing on one timer is what the TIMER_FIRED dedupe key is for."""
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.mark_fired(TimerId("tmr_01HQ"), NOW + timedelta(hours=49))
            with pytest.raises(NotFound):
                store.mark_fired(TimerId("tmr_01HQ"), NOW + timedelta(hours=49))


@pytest.mark.integration
class TestOneActiveTimerPerKind:
    def test_a_second_of_the_same_kind_is_refused(self, database: Path) -> None:
        """By the index, not by a check: two steps arming one reminder is a real race."""
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            with pytest.raises(Exception, match="UNIQUE"):
                store.schedule(a_timer(id=TimerId("tmr_02HQ")))

    def test_cancelling_frees_the_slot(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.cancel(TimerId("tmr_01HQ"), NOW, "state_left")
            store.schedule(a_timer(id=TimerId("tmr_02HQ")))
            assert len(store.active_for_case(CaseId("case_01HQ"))) == 1

    def test_different_kinds_coexist(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.schedule(a_timer(id=TimerId("tmr_02HQ"), kind=TimerKind.SELLER_DECISION))
            assert len(store.active_for_case(CaseId("case_01HQ"))) == 2


@pytest.mark.integration
class TestEvidenceRetriesArePerRound:
    """D41. The limit is three per gather round, not three per case."""

    def test_a_new_round_gets_its_own_allowance(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            for index in range(3):
                store.schedule(
                    a_timer(
                        id=TimerId(f"tmr_r1_{index:020d}"),
                        kind=TimerKind.EVIDENCE_RETRY,
                        gather_round=1,
                    )
                )
                store.cancel(TimerId(f"tmr_r1_{index:020d}"), NOW, "retried")

            assert store.retries_this_round(CaseId("case_01HQ"), 1) == 3
            # Round two starts at zero, not at the limit.
            assert store.retries_this_round(CaseId("case_01HQ"), 2) == 0

    def test_the_round_is_required_for_this_kind_and_forbidden_for_others(
        self, database: Path
    ) -> None:
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            with pytest.raises(DomainValidationError, match="EVIDENCE_RETRY"):
                store.schedule(a_timer(kind=TimerKind.EVIDENCE_RETRY, gather_round=None))
            with pytest.raises(DomainValidationError, match="EVIDENCE_RETRY"):
                store.schedule(a_timer(kind=TimerKind.SELLER_REMINDER, gather_round=1))


@pytest.mark.integration
class TestLeavingAStateCancelsItsTimers:
    def test_named_kinds_are_cancelled(self, database: Path) -> None:
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.schedule(a_timer(id=TimerId("tmr_02HQ"), kind=TimerKind.APPROVAL_EXPIRY))
            cancelled = store.cancel_kinds(
                CaseId("case_01HQ"), [TimerKind.APPROVAL_EXPIRY], NOW, "approval_decided"
            )
        assert cancelled == 1

    def test_cancel_all_clears_everything(self, database: Path) -> None:
        """T90 cancels all timers: a case with a person waits for the person, not a clock."""
        engine = create_sqlite_engine(database)
        with SqliteUnitOfWork(engine) as work:
            store = SqliteTimerStore(work.connection)
            store.schedule(a_timer())
            store.schedule(a_timer(id=TimerId("tmr_02HQ"), kind=TimerKind.SELLER_DECISION))
            assert store.cancel_all(CaseId("case_01HQ"), NOW, "escalated") == 2
            assert store.active_for_case(CaseId("case_01HQ")) == []
