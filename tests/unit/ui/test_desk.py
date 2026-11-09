"""The case desk (ADR 0046), on a real case with a person, applied by the real runner.

`kurta_box_larger` (SHP-000001) reaches NEEDS_HUMAN_REVIEW exactly as it did on the hosted
console: the rules engine will not judge image evidence (B5). Each decision is recorded the
desk's way - into the inbox, as the signed-in reviewer - and the runner's tick applies it
through dispatch and the transition's own guards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalSubject,
    CaseState,
    InboundEventType,
    ReviewCode,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.models import Actor, ReviewReason
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.ui.desk import CaseDesk, can_confirm_withdrawal

if TYPE_CHECKING:
    from collections.abc import Iterator

    from dispute_agent.api.full_run import World

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
ASHA = Actor(kind=ActorKind.HUMAN, id="human:asha")


@pytest.fixture
def with_a_person() -> Iterator[World]:
    world = open_world(
        "kurta_box_larger",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    assert world.report().case.state is CaseState.NEEDS_HUMAN_REVIEW
    yield world
    world.close()


def _decide(world: World, act: Any) -> None:
    with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
        case = work.cases.get(world.case_id)
        act(CaseDesk(inbox=work.inbox, clock=world.clock, reviewer=ASHA), case)
    world.tick()


def _events(world: World) -> list[Any]:
    with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
        return list(work.events.list_for_case(world.case_id))


def test_taking_over_hands_the_case_to_the_person(with_a_person: World) -> None:
    _decide(with_a_person, lambda desk, case: desk.take_over(case, notify_seller=False))
    assert with_a_person.report().case.state is CaseState.HANDED_TO_HUMAN
    took = [e for e in _events(with_a_person) if e.type == InboundEventType.HUMAN_TOOK_CASE]
    assert [e.actor.id for e in took] == ["human:asha"]


def test_taking_over_with_a_notice_puts_the_notice_through_review(with_a_person: World) -> None:
    _decide(with_a_person, lambda desk, case: desk.take_over(case, notify_seller=True))
    report = with_a_person.report()
    assert report.case.state is CaseState.AWAITING_APPROVAL
    with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
        pending = list(work.approvals.pending(with_a_person.case_id))
    assert [a.request.subject for a in pending] == [ApprovalSubject.MESSAGE]


def test_returning_runs_the_case_again_and_keeps_the_reason(with_a_person: World) -> None:
    reason = "The photo shows the box at dispatch; please look again."
    _decide(
        with_a_person,
        lambda desk, case: desk.return_to_agent(case, reason, credit_verified=False),
    )
    events = _events(with_a_person)
    returned = [e for e in events if e.type == InboundEventType.HUMAN_RETURNED_CASE]
    assert [e.actor.id for e in returned] == ["human:asha"]
    with with_a_person.engine.connect() as conn:  # the reason is kept with the inbox row
        rows = conn.exec_driver_sql("SELECT event_json FROM inbox").scalars().all()
    assert any(reason in str(row) for row in rows)
    moved = [getattr(e.payload, "transition_id", None) for e in events]
    assert "T37" in moved  # back to gathering evidence, through the engine's own transition


def test_returning_needs_a_reason_and_never_follows_a_verified_credit(
    with_a_person: World,
) -> None:
    with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
        case = work.cases.get(with_a_person.case_id)
        desk = CaseDesk(inbox=work.inbox, clock=with_a_person.clock, reviewer=ASHA)
        with pytest.raises(DomainValidationError, match="reason"):
            desk.return_to_agent(case, "   ", credit_verified=False)
        with pytest.raises(DomainValidationError, match="verified credit"):
            desk.return_to_agent(case, "look again", credit_verified=True)


def test_withdrawal_is_offered_only_when_the_seller_asked(with_a_person: World) -> None:
    with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
        case = work.cases.get(with_a_person.case_id)
        assert not can_confirm_withdrawal(case)  # with a person for B5, not a withdrawal
        desk = CaseDesk(inbox=work.inbox, clock=with_a_person.clock, reviewer=ASHA)
        with pytest.raises(DomainValidationError, match="withdraw"):
            desk.confirm_withdrawal(case)
        claimed = case.model_copy(
            update={"review_reason": ReviewReason(code=ReviewCode.SELLER_WITHDRAWAL_CLAIMED)}
        )
        assert can_confirm_withdrawal(claimed)


def test_a_case_no_longer_with_a_person_is_left_alone(with_a_person: World) -> None:
    _decide(with_a_person, lambda desk, case: desk.take_over(case, notify_seller=False))
    with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
        case = work.cases.get(with_a_person.case_id)
        desk = CaseDesk(inbox=work.inbox, clock=with_a_person.clock, reviewer=ASHA)
        with pytest.raises(DomainValidationError, match="no longer"):
            desk.take_over(case, notify_seller=False)


def test_only_a_signed_in_person_can_use_the_desk() -> None:
    system = Actor(kind=ActorKind.SYSTEM, id="system:runner")
    with pytest.raises(DomainValidationError):
        CaseDesk(inbox=None, clock=None, reviewer=system)  # type: ignore[arg-type]


def test_a_returned_case_shows_again_on_review_when_it_comes_back(with_a_person: World) -> None:
    """Review hid a case acted on this session for good, so a Return that came back to a
    person (on Agent steps) never reappeared on Review. Hidden only until the case changes."""
    from dispute_agent.ui.review import not_yet_applied
    from dispute_agent.ui.review import with_a_person as people

    def shown(acted: dict[str, str]) -> list[str]:
        with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
            return [p.case_id for p in not_yet_applied(people(work), acted)]

    case_id = str(with_a_person.case_id)
    with SqliteUnitOfWork(with_a_person.engine, clock=SystemClock()) as work:
        before = next(p for p in people(work) if p.case_id == case_id)
        desk = CaseDesk(inbox=work.inbox, clock=with_a_person.clock, reviewer=ASHA)
        desk.return_to_agent(
            work.cases.get(with_a_person.case_id), "Look again.", credit_verified=False
        )
    acted = {case_id: before.updated_at.isoformat()}  # what the Review screen records
    assert shown(acted) == []  # not offered twice while the agent has not applied it

    with_a_person.clock.advance(timedelta(seconds=30))
    with_a_person.tick()  # the agent picks up the Return and runs its new round
    with_a_person.drive(20)
    assert with_a_person.report().case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert shown(acted) == [case_id]  # back with a person: a new question, shown again
