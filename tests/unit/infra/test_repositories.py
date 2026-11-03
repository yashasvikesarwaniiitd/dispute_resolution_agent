"""Card P1-02: the repositories, and the two guarantees they exist for.

Optimistic versioning is not a nicety. One worker is handling a case because a seller
replied; another is handling the same case because a timer fired. Both read version 3. If the
second save silently overwrote the first, the case would end in a state nobody decided, with
an audit trail that does not explain it. `save` makes the loser find out.

Append-only is the other. The events table is the only record of why anything happened, and
it is worth having only if nothing can quietly rewrite it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork, unit_of_work_factory
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    CauseKind,
    InboundEventType,
    InternalEventType,
    ReviewCode,
    Trigger,
)
from dispute_agent.kernel.domain.errors import (
    ConcurrencyConflict,
    DuplicateEvent,
    NotFound,
)
from dispute_agent.kernel.domain.events import Event, EventPayload, StateChangedPayload, StepPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, DedupeKey, EventId
from dispute_agent.kernel.domain.models import Actor, Case, Cause, RelatedCase, ReviewReason

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
SYSTEM = Actor(kind=ActorKind.SYSTEM, id="system")
STEP_CAUSE = Cause(kind=CauseKind.STEP, ref="eligibility")


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


@pytest.fixture
def uow(engine):
    return unit_of_work_factory(engine)


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


def a_case(case_id: str = "case_01HQ", **overrides: object) -> Case:
    fields: dict[str, object] = {
        "id": CaseId(case_id),
        "pack": "weight_dispute",
        "facts": facts(),
        "state": CaseState.RECEIVED,
        "version": 1,
        "correlation_id": CorrelationId("corr_01HQ"),
        "opened_at": NOW,
        "updated_at": NOW,
        "state_entered_at": NOW,
    }
    fields.update(overrides)
    return Case(**fields)


def an_event(event_id: str = "evt_01HQ", **overrides: object) -> Event:
    fields: dict[str, object] = {
        "id": EventId(event_id),
        "case_id": CaseId("case_01HQ"),
        "type": InboundEventType.DISPUTE_RAISED,
        "occurred_at": NOW,
        "recorded_at": NOW,
        "actor": Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        "cause": Cause(kind=CauseKind.INPUT_EVENT, ref="inb_01HQ"),
        "correlation_id": CorrelationId("corr_01HQ"),
        "dedupe_key": DedupeKey("dispute_api:form-1"),
        # DISPUTE_RAISED has no concrete payload class yet (events-catalog.md arrives
        # with P1-05), so it round-trips as the union root.
        "payload": EventPayload(),
    }
    fields.update(overrides)
    return Event(**fields)


class TestRoundTrip:
    def test_a_case_survives_a_round_trip(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
        with uow() as work:
            loaded = work.cases.get(CaseId("case_01HQ"))
        assert loaded == a_case()

    def test_the_facts_survive_too(self, uow) -> None:
        """Money inside CaseFacts is a Decimal; JSON round-tripping it badly would be
        invisible until a credit was a rupee out (I9)."""
        from dispute_agent.kernel.domain.values import Money, UntrustedText

        statement = UntrustedText.create("the parcel was 480 grams", "dispute_form", "frm_1")
        original = a_case(facts=facts(disputed_amount=Money.of("152.00"), statement=statement))
        with uow() as work:
            work.cases.insert(original)
        with uow() as work:
            loaded = work.cases.get(CaseId("case_01HQ"))
        assert loaded.facts.disputed_amount == Money.of("152.00")
        assert loaded.facts.statement == statement

    def test_a_missing_case_raises_not_found(self, uow) -> None:
        with uow() as work, pytest.raises(NotFound):
            work.cases.get(CaseId("case_nope"))


class TestOptimisticVersioning:
    def test_two_readers_at_version_one_and_the_second_loses(self, uow) -> None:
        """The card's named test, and the reason the class exists."""
        with uow() as work:
            work.cases.insert(a_case())

        with uow() as work:
            first = work.cases.get(CaseId("case_01HQ"))
        with uow() as work:
            second = work.cases.get(CaseId("case_01HQ"))
        assert first.version == second.version == 1

        with uow() as work:
            work.cases.save(first.model_copy(update={"state": CaseState.GATHERING_EVIDENCE}), 1)

        with uow() as work, pytest.raises(ConcurrencyConflict, match="version 1"):
            work.cases.save(
                second.model_copy(
                    update={"state": CaseState.REJECTED_INELIGIBLE, "closed_at": NOW}
                ),
                1,
            )

    def test_a_successful_save_returns_the_bumped_version(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
        with uow() as work:
            saved = work.cases.save(a_case(state=CaseState.GATHERING_EVIDENCE), 1)
        assert saved.version == 2
        with uow() as work:
            assert work.cases.get(CaseId("case_01HQ")).version == 2

    def test_saving_an_unknown_case_raises_not_found(self, uow) -> None:
        with uow() as work, pytest.raises(NotFound):
            work.cases.save(a_case("case_nope"), 1)

    def test_a_new_case_must_start_at_version_one(self, uow) -> None:
        with uow() as work, pytest.raises(ConcurrencyConflict):
            work.cases.insert(a_case(version=4))


class TestOneOpenCase:
    def test_a_second_open_case_for_one_pair_is_refused(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
        with uow() as work, pytest.raises(ConcurrencyConflict):
            work.cases.insert(a_case("case_02HQ"))

    def test_find_open_returns_it(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
        with uow() as work:
            found = work.cases.find_open("SHP-0001", "CHG-0001")
        assert found is not None
        assert found.id == CaseId("case_01HQ")

    def test_find_open_ignores_a_closed_case(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
            work.cases.save(a_case(state=CaseState.REJECTED_INELIGIBLE, closed_at=NOW), 1)
        with uow() as work:
            assert work.cases.find_open("SHP-0001", "CHG-0001") is None
            assert work.cases.find_latest("SHP-0001", "CHG-0001") is not None


class TestEventLogIsAppendOnly:
    def test_the_port_exposes_no_mutation(self) -> None:
        """I8, checked against the class rather than hoped for."""
        from dispute_agent.infra.sqlite.event_log import SqliteEventLog

        for forbidden in ("update", "delete", "remove", "edit"):
            assert not hasattr(SqliteEventLog, forbidden)

    def test_a_direct_update_is_refused_by_the_trigger(self, engine) -> None:
        """Even bypassing the repository entirely."""
        from sqlalchemy import text

        with SqliteUnitOfWork(engine) as work:
            work.cases.insert(a_case())
            work.events.append(an_event())

        with engine.begin() as conn, pytest.raises(Exception, match="append_only"):
            conn.execute(text("UPDATE events SET payload_json='{}' WHERE id='evt_01HQ'"))

    def test_a_direct_delete_is_refused_too(self, engine) -> None:
        from sqlalchemy import text

        with SqliteUnitOfWork(engine) as work:
            work.cases.insert(a_case())
            work.events.append(an_event())

        with engine.begin() as conn, pytest.raises(Exception, match="append_only"):
            conn.execute(text("DELETE FROM events WHERE id='evt_01HQ'"))


class TestSequenceNumbers:
    def test_append_assigns_the_next_number(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
            first = work.events.append(an_event())
            second = work.events.append(
                an_event(
                    "evt_02HQ",
                    type=InternalEventType.STATE_CHANGED,
                    dedupe_key=None,
                    causation_id=EventId("evt_01HQ"),
                    cause=STEP_CAUSE,
                    actor=SYSTEM,
                    payload=StateChangedPayload(
                        from_state=CaseState.RECEIVED,
                        to_state=CaseState.GATHERING_EVIDENCE,
                        transition_id="T01",
                        trigger=Trigger.CASE_OPENED,
                    ),
                )
            )
        assert (first.seq, second.seq) == (1, 2)

    def test_the_kernel_builds_events_at_seq_zero(self) -> None:
        """The number is the log's to assign, so nothing upstream can guess it wrong."""
        assert an_event().seq == 0

    def test_a_repeated_dedupe_key_raises(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
            work.events.append(an_event())
            with pytest.raises(DuplicateEvent):
                work.events.append(an_event("evt_02HQ"))

    def test_an_event_for_an_unknown_case_raises(self, uow) -> None:
        with uow() as work, pytest.raises(NotFound):
            work.events.append(an_event(case_id=CaseId("case_nope")))

    def test_events_come_back_in_order_with_their_cause(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
            work.events.append(an_event())
            work.events.append(
                an_event(
                    "evt_02HQ",
                    dedupe_key=None,
                    causation_id=EventId("evt_01HQ"),
                    type=InternalEventType.CASE_OPENED,
                    cause=STEP_CAUSE,
                    actor=SYSTEM,
                    payload=StepPayload(step="intake"),
                )
            )
        with uow() as work:
            listed = work.events.list_for_case(CaseId("case_01HQ"))
        assert [e.seq for e in listed] == [1, 2]
        assert listed[1].cause.kind is CauseKind.STEP
        assert listed[1].cause.ref == "eligibility"

    def test_a_state_change_payload_survives(self, uow) -> None:
        """The transition id is what lets a row be traced back to the rule that allowed it."""
        payload = StateChangedPayload(
            from_state=CaseState.RECEIVED,
            to_state=CaseState.NEEDS_HUMAN_REVIEW,
            transition_id="T90",
            trigger=Trigger.ESCALATE,
            review_reason=ReviewReason(code=ReviewCode.STEP_LIMIT),
        )
        with uow() as work:
            work.cases.insert(a_case())
            raised = work.events.append(an_event())
            work.events.append(
                an_event(
                    "evt_02HQ",
                    type=InternalEventType.STATE_CHANGED,
                    dedupe_key=None,
                    causation_id=raised.id,
                    cause=STEP_CAUSE,
                    actor=SYSTEM,
                    payload=payload,
                )
            )
        with uow() as work:
            stored = work.events.list_for_case(CaseId("case_01HQ"))[1]
        assert isinstance(stored.payload, StateChangedPayload)
        assert stored.payload.transition_id == "T90"
        assert stored.payload.review_reason is not None


class TestTransactions:
    def test_a_failing_step_writes_nothing(self, uow, engine) -> None:
        """One step, one transaction: a case row and its events never disagree."""
        from sqlalchemy import text

        with pytest.raises(RuntimeError, match="step failed"), uow() as work:
            work.cases.insert(a_case())
            work.events.append(an_event())
            raise RuntimeError("step failed")

        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM cases")).scalar() == 0
            assert conn.execute(text("SELECT count(*) FROM events")).scalar() == 0

    def test_a_successful_step_commits_everything_together(self, uow, engine) -> None:
        from sqlalchemy import text

        with uow() as work:
            work.cases.insert(a_case())
            work.events.append(an_event())

        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM cases")).scalar() == 1
            assert conn.execute(text("SELECT count(*) FROM events")).scalar() == 1


class TestRelatedCases:
    def test_it_excludes_the_case_itself_and_reports_outcome_only(self, uow) -> None:
        with uow() as work:
            work.cases.insert(a_case())
            work.cases.save(a_case(state=CaseState.REJECTED_INELIGIBLE, closed_at=NOW), 1)
            work.cases.insert(
                a_case(
                    "case_02HQ",
                    facts=facts(claim_ref="CHG-0002"),
                    opened_at=NOW + timedelta(hours=1),
                    updated_at=NOW + timedelta(hours=1),
                    state_entered_at=NOW + timedelta(hours=1),
                )
            )
        with uow() as work:
            current = work.cases.get(CaseId("case_02HQ"))
            related = work.cases.related(current)
        assert [r.case_id for r in related] == [CaseId("case_01HQ")]
        assert related[0].state is CaseState.REJECTED_INELIGIBLE
        assert set(RelatedCase.model_fields) == {"case_id", "state", "closed_at", "discrepancy"}


class TestRestartRecovery:
    def test_cases_in_a_state_come_back_oldest_first(self, uow) -> None:
        later = NOW + timedelta(hours=2)
        with uow() as work:
            work.cases.insert(a_case())
            work.cases.insert(
                a_case(
                    "case_02HQ",
                    facts=facts(claim_ref="CHG-0002"),
                    opened_at=later,
                    updated_at=later,
                    state_entered_at=later,
                )
            )
        with uow() as work:
            found = work.cases.list_in_states([CaseState.RECEIVED])
        assert [c.id for c in found] == [CaseId("case_01HQ"), CaseId("case_02HQ")]
