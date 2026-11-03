"""Card P1-05: a dispute enters the system exactly once.

Scenario S11 is the one to read first: the same event submitted twice must yield one case and
one input event. Not "usually one" — the dedupe key is a UNIQUE index, so the second
submission is refused by the database rather than by a check that two requests could race
past.

Scenario S27 is the other: a dispute naming a seller who is not on the test allowlist is
refused with nothing written at all. This project only ever writes to the owner's own test
mailbox (I11), and a case that exists but can never be safely acted on is worse than no case.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    InboundEventType,
    InboxStatus,
    InternalEventType,
)
from dispute_agent.kernel.domain.errors import DomainValidationError, DuplicateEvent
from dispute_agent.kernel.domain.events import EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.orchestrator.intake import (
    Intake,
    RecipientNotAllowedError,
    check_recipient,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
ALLOWED = ("seller@example.invalid",)
WINDOW = timedelta(days=7)
INPUT = EventId("evt_input0000000000000000000")


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


def facts(**overrides: object) -> CaseFacts:
    fields: dict[str, object] = {
        "shipment_ref": "SHP-0001",
        "claim_ref": "CHG-0001",
        "seller_ref": "SELLER-0001",
        "seller_contact": "seller@example.invalid",
        "raised_at": NOW,
    }
    fields.update(overrides)
    return CaseFacts(**fields)


def an_inbound(source_event_id: str = "form-1", **overrides: object) -> InboundEvent:
    fields: dict[str, object] = {
        "type": InboundEventType.DISPUTE_RAISED,
        "source": "dispute_api",
        "source_event_id": source_event_id,
        "occurred_at": NOW,
        "actor": Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        "payload": InboundPayload(),
    }
    fields.update(overrides)
    return InboundEvent(**fields)


def intake() -> Intake:
    return Intake(allowed_recipients=ALLOWED, reopen_window=WINDOW)


def open_case(work: SqliteUnitOfWork, case_id: str = "case_01HQ", **facts_overrides: object):
    result = intake().open_or_get_case(
        work.cases,
        an_inbound(),
        facts(**facts_overrides),
        pack="weight_dispute",
        now=NOW,
        case_id=CaseId(case_id),
        correlation_id=CorrelationId("corr_01HQ"),
        opened_event_id=EventId("evt_opened000000000000000000"),
        causation_id=INPUT,
    )
    if result.created:
        work.cases.insert(result.case)
    return result


class TestDeduplication:
    def test_the_same_event_twice_yields_one_inbox_row(self, engine) -> None:
        """Scenario S11. The second submission is refused by a UNIQUE index."""
        with SqliteUnitOfWork(engine) as work:
            inbox = SqliteInboxStore(work.connection)
            first = inbox.accept(an_inbound(), NOW)

        with SqliteUnitOfWork(engine) as work:
            inbox = SqliteInboxStore(work.connection)
            with pytest.raises(DuplicateEvent) as caught:
                inbox.accept(an_inbound(), NOW)

        assert caught.value.existing_inbox_id == first

    def test_the_duplicate_names_the_original(self, engine) -> None:
        """So a repeat submission gets the same answer as the first, not an error."""
        with SqliteUnitOfWork(engine) as work:
            inbox = SqliteInboxStore(work.connection)
            first = inbox.accept(an_inbound(), NOW)
        with SqliteUnitOfWork(engine) as work, pytest.raises(DuplicateEvent) as caught:
            SqliteInboxStore(work.connection).accept(an_inbound(), NOW)
        assert str(caught.value.existing_inbox_id) == str(first)

    def test_a_different_source_event_is_not_a_duplicate(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            inbox = SqliteInboxStore(work.connection)
            first = inbox.accept(an_inbound("form-1"), NOW)
            second = inbox.accept(an_inbound("form-2"), NOW)
        assert first != second

    def test_the_inbox_id_is_a_function_of_the_event(self, engine) -> None:
        """A replay produces the same id, so idempotency reaches all the way down."""
        with SqliteUnitOfWork(engine) as work:
            first = SqliteInboxStore(work.connection).accept(an_inbound(), NOW)
        with SqliteUnitOfWork(engine) as work, pytest.raises(DuplicateEvent) as caught:
            SqliteInboxStore(work.connection).accept(an_inbound(), NOW)
        assert caught.value.existing_inbox_id == first

    def test_the_dedupe_key_is_source_and_event_id(self) -> None:
        assert str(an_inbound().dedupe_key) == "dispute_api:form-1"


class TestOneOpenCase:
    def test_a_first_dispute_opens_a_case(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            result = open_case(work)
        assert result.created is True
        assert result.case.state is CaseState.RECEIVED
        assert result.case.version == 1

    def test_opening_records_a_case_opened_event(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            result = open_case(work)
        assert [e.type for e in result.events] == [InternalEventType.CASE_OPENED]
        assert result.events[0].causation_id == INPUT

    def test_a_second_dispute_for_the_same_pair_returns_the_open_case(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            first = open_case(work)
        with SqliteUnitOfWork(engine) as work:
            second = open_case(work, "case_02HQ")
        assert second.created is False
        assert second.case.id == first.case.id

    def test_a_different_claim_opens_its_own_case(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            open_case(work)
        with SqliteUnitOfWork(engine) as work:
            second = open_case(work, "case_02HQ", claim_ref="CHG-0002")
        assert second.created is True


#: Legal routes to a closed state. The database refuses any shortcut (tr_cases_legal_transition),
#: which is the point - a test that could jump straight to RESOLVED_UPHELD would be testing a
#: case the system can never actually produce.
ROUTES: dict[CaseState, tuple[CaseState, ...]] = {
    CaseState.RESOLVED_UPHELD: (
        CaseState.GATHERING_EVIDENCE,
        CaseState.CALCULATING,
        CaseState.INVESTIGATING,
        CaseState.EXECUTING,
        CaseState.VERIFYING,
        CaseState.RESOLVED_UPHELD,
    ),
    CaseState.REJECTED_INELIGIBLE: (CaseState.REJECTED_INELIGIBLE,),
}


class TestReopening:
    def close_as(self, engine, state: CaseState, closed_at: datetime) -> None:
        from dispute_agent.kernel.domain.enums import TERMINAL_STATES

        with SqliteUnitOfWork(engine) as work:
            open_case(work)
            for step in ROUTES[state]:
                current = work.cases.get(CaseId("case_01HQ"))
                work.cases.save(
                    current.model_copy(
                        update={
                            "state": step,
                            "closed_at": closed_at if step in TERMINAL_STATES else None,
                        }
                    ),
                    current.version,
                )

    def test_a_soft_terminal_case_inside_the_window_is_reopened(self, engine) -> None:
        self.close_as(engine, CaseState.RESOLVED_UPHELD, NOW)
        with SqliteUnitOfWork(engine) as work:
            result = intake().open_or_get_case(
                work.cases,
                an_inbound("form-2"),
                facts(),
                pack="weight_dispute",
                now=NOW + timedelta(days=2),
                case_id=CaseId("case_02HQ"),
                correlation_id=CorrelationId("corr_02HQ"),
                opened_event_id=EventId("evt_opened000000000000000001"),
                causation_id=INPUT,
            )
        assert result.created is False
        assert result.reopened_from == CaseId("case_01HQ")

    def test_outside_the_window_a_new_case_is_opened(self, engine) -> None:
        self.close_as(engine, CaseState.RESOLVED_UPHELD, NOW)
        with SqliteUnitOfWork(engine) as work:
            result = intake().open_or_get_case(
                work.cases,
                an_inbound("form-2"),
                facts(),
                pack="weight_dispute",
                now=NOW + timedelta(days=30),
                case_id=CaseId("case_02HQ"),
                correlation_id=CorrelationId("corr_02HQ"),
                opened_event_id=EventId("evt_opened000000000000000001"),
                causation_id=INPUT,
            )
        assert result.created is True
        assert result.case.prior_case_id == CaseId("case_01HQ")

    def test_a_hard_terminal_case_is_never_reopened_by_the_agent(self, engine) -> None:
        """A credited dispute reopening itself is how one shipment gets paid twice."""
        self.close_as(engine, CaseState.REJECTED_INELIGIBLE, NOW)
        with SqliteUnitOfWork(engine) as work:
            result = intake().open_or_get_case(
                work.cases,
                an_inbound("form-2"),
                facts(),
                pack="weight_dispute",
                now=NOW + timedelta(days=1),
                case_id=CaseId("case_02HQ"),
                correlation_id=CorrelationId("corr_02HQ"),
                opened_event_id=EventId("evt_opened000000000000000001"),
                causation_id=INPUT,
            )
        assert result.created is True
        assert result.reopened_from is None


class TestTheRecipientAllowlist:
    def test_an_unlisted_seller_is_refused(self) -> None:
        """Scenario S27, I11."""
        with pytest.raises(RecipientNotAllowedError, match="allowlist"):
            check_recipient("stranger@example.com", ALLOWED)

    def test_nothing_is_written_when_it_is_refused(self, engine) -> None:
        """The check comes before any store is touched, so there is no case to clean up."""
        from sqlalchemy import text

        with SqliteUnitOfWork(engine) as work, pytest.raises(RecipientNotAllowedError):
            open_case(work, seller_contact="stranger@example.com")

        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM cases")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM inbox")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM events")).scalar() == 0

    def test_the_check_is_case_insensitive(self) -> None:
        check_recipient("Seller@Example.Invalid", ALLOWED)

    def test_an_empty_allowlist_refuses_everything(self) -> None:
        """A misconfigured deployment that emails strangers is worse than one that does
        nothing, and an empty list is far more likely to be an oversight."""
        with pytest.raises(RecipientNotAllowedError):
            check_recipient("seller@example.invalid", ())

    def test_it_is_its_own_error_type(self) -> None:
        """The API answers this with 422 and stores nothing, unlike other validation."""
        assert issubclass(RecipientNotAllowedError, DomainValidationError)


class TestInboxStatuses:
    def test_a_new_row_is_pending(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work:
            store = SqliteInboxStore(work.connection)
            inbox_id = store.accept(an_inbound(), NOW)
            assert store.get(inbox_id)["status"] == InboxStatus.PENDING.value

    def test_processed_needs_a_case_and_an_event(self, engine) -> None:
        """The schema refuses the status without them: processed but belonging to no case
        is a contradiction."""
        with SqliteUnitOfWork(engine) as work:
            store = SqliteInboxStore(work.connection)
            inbox_id = store.accept(an_inbound(), NOW)
            result = open_case(work)
            event = work.events.append(
                __import__("dispute_agent.kernel.domain.events", fromlist=["Event"]).Event(
                    id=EventId("evt_raised00000000000000000"),
                    case_id=result.case.id,
                    type=InboundEventType.DISPUTE_RAISED,
                    occurred_at=NOW,
                    recorded_at=NOW,
                    actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
                    cause=result.events[0].cause,
                    correlation_id=result.case.correlation_id,
                    dedupe_key=an_inbound().dedupe_key,
                    payload=EventPayload(),
                )
            )
            store.mark_processed(inbox_id, result.case.id, event.id)
            assert store.get(inbox_id)["status"] == InboxStatus.PROCESSED.value

    def test_unroutable_keeps_the_row(self, engine) -> None:
        """Nothing is lost: a human can look at what could not be placed."""
        with SqliteUnitOfWork(engine) as work:
            store = SqliteInboxStore(work.connection)
            inbox_id = store.accept(an_inbound(), NOW)
            store.mark_unroutable(inbox_id, "no matching case")
            row = store.get(inbox_id)
        assert row["status"] == InboxStatus.UNROUTABLE.value
        assert row["last_error"] == "no matching case"


class TestIntakeOnlyOpensFromDisputeRaised:
    def test_another_event_type_is_refused(self, engine) -> None:
        with SqliteUnitOfWork(engine) as work, pytest.raises(DomainValidationError):
            intake().open_or_get_case(
                work.cases,
                an_inbound(type=InboundEventType.SELLER_REPLY_RECEIVED),
                facts(),
                pack="weight_dispute",
                now=NOW,
                case_id=CaseId("case_01HQ"),
                correlation_id=CorrelationId("corr_01HQ"),
                opened_event_id=EventId("evt_opened000000000000000000"),
                causation_id=INPUT,
            )
