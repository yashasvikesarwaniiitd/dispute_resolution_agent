"""An action the agent could not confirm, as the console shows it (live demo, 2026-11-22).

Three things went wrong on the hosted console after an email's fate became unknown:

- Agent steps titled it "Sent the explanation email", though nothing proved it went out.
- Review had no way to say whether it happened, so a Return was refused (T37) silently.
- The refused Return left the case unchanged, and Review hid it for the rest of the session.

Also here: every time is shown in India Standard Time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import MessagePayload
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ActorKind,
    CaseState,
    MessageKind,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.ui.desk import NOT_DONE, CaseDesk
from dispute_agent.ui.labels import clock, when
from dispute_agent.ui.review import HOLD_S, not_yet_applied, unknown_actions
from dispute_agent.ui.trail_words import executed, went_out

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")
EMAIL = MessagePayload(
    kind=MessageKind.UPHELD_EXPLANATION, to="seller@example.invalid", subject="s", body="b"
)


def _action(status: ActionStatus, **fields: object) -> Any:  # Any: a stand-in for an Action
    base: dict[str, object] = {
        "status": status,
        "sent_at": None,
        "external_ref": None,
        "last_error_code": None,
    }
    return SimpleNamespace(**{**base, **fields})


class TestTimesAreIndian:
    def test_a_time_is_shown_in_ist(self) -> None:
        moment = datetime(2026, 11, 22, 15, 52, tzinfo=UTC)
        assert clock(moment) == "21:22"
        assert when(moment) == "22 Nov 2026, 21:22 IST"

    def test_late_evening_utc_is_already_tomorrow_in_india(self) -> None:
        assert when(datetime(2026, 11, 22, 20, 0, tzinfo=UTC)) == "23 Nov 2026, 01:30 IST"

    def test_a_naive_stored_time_is_read_as_utc(self) -> None:
        assert clock(datetime(2026, 11, 22, 13, 18)) == "18:48"


class TestTheTimelineNeverClaimsASendItCannotProve:
    def test_an_unknown_email_that_never_went_out_is_not_called_sent(self) -> None:
        action = _action(ActionStatus.UNKNOWN, last_error_code="recovery_unconfirmed")
        title, summary, lines = executed(action, EMAIL)
        assert not went_out(action)
        assert title == "Could not confirm whether the explanation email went out"
        assert "will not try again" in summary
        assert lines == ("Recorded reason: recovery_unconfirmed",)

    def test_a_failed_email_says_nothing_went_out(self) -> None:
        title, summary, _ = executed(_action(ActionStatus.FAILED), EMAIL)
        assert title == "Could not send the explanation email"
        assert "Nothing went out" in summary

    def test_an_email_gmail_accepted_is_sent(self) -> None:
        sent = _action(ActionStatus.SENT, sent_at=NOW, external_ref="m1")
        assert executed(sent, EMAIL)[0] == "Sent the explanation email"
        # Sent but not proven by the read-back: still sent; the verify step says the rest.
        unproven = _action(ActionStatus.UNKNOWN, sent_at=NOW, external_ref="m1")
        assert executed(unproven, EMAIL)[0] == "Sent the explanation email"


@pytest.fixture
def unknown_credit() -> Any:
    """S13b: the ledger posts the wrong amount, so the credit is UNKNOWN and with a person."""
    world = open_world(
        "calc_error_wrong_slab",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        ledger_failure="wrong_amount",
    )
    assert world.drive().case.state is CaseState.NEEDS_HUMAN_REVIEW
    yield world
    world.close()


def _credit(world: Any) -> Any:
    with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
        return next(
            a
            for a in work.actions.for_case(world.case_id)
            if a.action_type is ActionType.POST_CREDIT
        )


class TestReviewAsksWhatHappened:
    def test_the_card_lists_the_unknown_action_and_where_to_check(
        self, unknown_credit: Any
    ) -> None:
        with SqliteUnitOfWork(unknown_credit.engine, clock=SystemClock()) as work:
            found = unknown_actions(work, unknown_credit.case_id)
        assert [u.action_id for u in found] == [_credit(unknown_credit).id]
        assert found[0].what.startswith("the credit of ₹")
        assert found[0].where == "the ledger"

    def test_a_return_that_does_not_answer_is_refused_where_the_person_sees_it(
        self, unknown_credit: Any
    ) -> None:
        credit = _credit(unknown_credit)
        with SqliteUnitOfWork(unknown_credit.engine, clock=SystemClock()) as work:
            case = work.cases.get(unknown_credit.case_id)
            desk = CaseDesk(inbox=work.inbox, clock=unknown_credit.clock, reviewer=OWNER)
            with pytest.raises(DomainValidationError, match="actually happened"):
                desk.return_to_agent(
                    case, "Look again.", credit_verified=False, unknown=[credit.id]
                )

    def test_answering_not_done_returns_the_case_to_the_agent(self, unknown_credit: Any) -> None:
        credit = _credit(unknown_credit)
        with SqliteUnitOfWork(unknown_credit.engine, clock=SystemClock()) as work:
            case = work.cases.get(unknown_credit.case_id)
            CaseDesk(inbox=work.inbox, clock=unknown_credit.clock, reviewer=OWNER).return_to_agent(
                case,
                "Checked the ledger: no credit was posted.",
                credit_verified=False,
                unknown=[credit.id],
                resolutions={credit.id: NOT_DONE},
            )
        unknown_credit.tick()
        assert _credit(unknown_credit).status is ActionStatus.FAILED
        assert _credit(unknown_credit).last_error_code == "human_confirmed_not_done"
        with SqliteUnitOfWork(unknown_credit.engine, clock=SystemClock()) as work:
            moved = [
                getattr(e.payload, "transition_id", None)
                for e in work.events.list_for_case(unknown_credit.case_id)
            ]
        assert "T37" in moved  # back to the agent, through the engine's own transition


class TestARefusedDecisionDoesNotHideTheCase:
    def _ticket(self) -> Any:  # Any: a stand-in for a Ticket
        return SimpleNamespace(case_id="case_1", updated_at=NOW)

    def test_hidden_just_after_acting_then_shown_again(self) -> None:
        people = [self._ticket()]
        acted = {"case_1": NOW.isoformat()}
        at = {"case_1": 1000.0}
        assert not_yet_applied(people, acted, acted_at=at, now=1000.0 + 5) == []
        later = 1000.0 + HOLD_S + 1
        assert not_yet_applied(people, acted, acted_at=at, now=later) == people

    def test_a_changed_case_shows_at_once(self) -> None:
        moved: Any = SimpleNamespace(case_id="case_1", updated_at=NOW + timedelta(seconds=3))
        acted = {"case_1": NOW.isoformat()}
        assert not_yet_applied([moved], acted, acted_at={"case_1": 0.0}, now=1.0) == [moved]
