"""The four screens, rendered by Streamlit's test runner over a real case (ADR 0044).

A case is driven through the whole line offline (the rules engine deciding) until a credit
waits for approval; its stores are copied into a temporary `data/` and each screen runs
against them, as `make run-console` would. The reviewer signs in with a token made for this
test, sees the recommendation beside the exact credit, approves, and the store records the
decision under the reviewer's name - nothing is sent from the page (I2).
"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from streamlit.testing.v1 import AppTest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.engine import create_sqlite_engine
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ApprovalDecision, CaseState
from dispute_agent.kernel.policy.config import PolicyConfig

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def _copy(source: Path, target: Path) -> None:
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)


@pytest.fixture
def desk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    world = open_world(
        "calc_error_high_value",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    _copy(world.folder / "ledger.sqlite3", data / "ledger.sqlite3")
    token = secrets.token_hex(16)  # made for this test, never a real one
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", token)
    monkeypatch.setenv("TEST_SELLER_EMAIL", "owner+seller@example.test")
    return {"token": token, "cases": data / "cases.sqlite3"}


def _seller() -> None:
    from dispute_agent.ui.screens import seller

    seller.page()


def _trail() -> None:
    from dispute_agent.ui.screens import trail

    trail.page()


def _review() -> None:
    from dispute_agent.ui.screens import reviewer

    reviewer.page()


def _ledger() -> None:
    from dispute_agent.ui.screens import finance

    finance.page()


def _text(at: AppTest) -> str:
    parts = (
        [m.value for m in at.markdown] + [t.value for t in at.text] + [c.value for c in at.caption]
    )
    return "\n".join(str(p) for p in parts)


def test_the_seller_sees_their_shipments_and_their_ticket(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_seller, default_timeout=60).run()
    assert not at.exception
    assert at.title[0].value == "Raise a ticket"
    assert len(at.dataframe) == 2  # shipments, and the one ticket


def test_the_steps_tell_the_case_in_order(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_trail, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    for expected in (
        "Waiting for a reviewer to approve a ₹1,820.00 credit.",  # the headline
        "Approve or reject it on the Review screen",  # what happens next
        "Round 1: First look",
        "Dispute raised on SHP-000002",
        "Charged ₹2,800.00, correct ₹980.00, overcharged ₹1,820.00.",
        "Proposed: Credit the seller",
        "a person must approve first",
        "Overcharge: ₹1,820.00",  # in the step's details
        "Its reasoning, exactly as recorded:",  # the proposal's rationale, shown openly
    ):
        assert expected in text


def test_the_steps_explain_a_shipment_closed_after_its_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = open_world(
        "not_delivered_late_scan",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    monkeypatch.chdir(tmp_path)

    at = AppTest.from_function(_trail, default_timeout=60).run()
    assert not at.exception
    text = _text(at)
    assert "Closed: not eligible" in text and "scan history shows" in text
    assert "not reached" in text  # approval and action were never reached
    assert "Overcharged" not in text  # no figures for a shipment that cannot be disputed


def test_a_reviewer_signs_in_sees_the_recommendation_and_approves(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    assert not at.exception
    at.text_input[0].input(desk["token"])
    assert at.selectbox[0].label == "Review as" and at.selectbox[0].options == ["owner"]
    at.button[0].click().run()
    assert not at.exception
    text = _text(at)
    assert "What the agent recommends" in text and "Credit the seller" in text
    assert "₹1,820.00" in text and "Rules engine" in text

    approve = next(b for b in at.button if b.label == "Approve")
    approve.click().run()
    assert not at.exception
    with SqliteUnitOfWork(create_sqlite_engine(desk["cases"]), clock=SystemClock()) as work:
        case = work.cases.list_in_states(list(CaseState))[0]
        decided = list(work.approvals.for_case(case.id))
    assert [(a.decision, a.decided_by.id if a.decided_by else "") for a in decided] == [
        (ApprovalDecision.APPROVED, "human:owner")
    ]


def test_a_wrong_token_shows_no_queue(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    at.text_input[0].input("not-the-token")
    at.button[0].click().run()
    assert at.error and "not correct" in at.error[0].value
    assert "What the agent recommends" not in _text(at)


def test_the_ledger_totals_are_sums_of_its_rows(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_ledger, default_timeout=60).run()
    assert not at.exception
    values = {m.label: m.value for m in at.metric}
    assert values["Credited back"] == "₹0.00"  # nothing approved yet
    assert values["Charged"].startswith("₹")  # money reads the same on every screen
    assert "No credits yet" in _text(at)


@pytest.fixture
def desk_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """SHP-000001 with a person (B5: image evidence), as on the hosted console."""
    world = open_world(
        "kurta_box_larger",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    world.drive(20)
    world.close()
    data = tmp_path / "data"
    data.mkdir()
    _copy(world.folder / "cases.sqlite3", data / "cases.sqlite3")
    token = secrets.token_hex(16)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", token)
    return {"token": token, "cases": data / "cases.sqlite3"}


def test_a_reviewer_takes_over_a_case_with_a_person(desk_case: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    at.text_input[0].input(desk_case["token"])
    at.button[0].click().run()
    assert not at.exception
    text = _text(at)
    assert "Why it is with a person" in text and "proposal escalated" in text
    assert "image evidence" in text  # the reason's detail, shown as text

    take = next(b for b in at.button if b.label == "Take over")
    take.click().run()
    assert not at.exception
    with sqlite3.connect(desk_case["cases"]) as conn:
        rows = conn.execute(
            "SELECT event_type, source, event_json FROM inbox WHERE event_type = 'HUMAN_TOOK_CASE'"
        ).fetchall()
    assert len(rows) == 1 and rows[0][1] == "ops_console" and "human:owner" in rows[0][2]


class TestHostedReviewSignIn:
    """ADR 0047: past the hosted site password, the reviewer chooses a listed approver."""

    def test_past_the_site_password_only_listed_approvers_are_offered(
        self, desk: dict[str, Any]
    ) -> None:
        at = AppTest.from_function(_review, default_timeout=60)
        at.session_state["gate_passed"] = True  # set only by the hosted gate, after the password
        at.run()
        assert not at.text_input  # no token, and no free-text name
        assert at.selectbox[0].label == "Review as" and at.selectbox[0].options == ["owner"]
        at.button[0].click().run()
        assert not at.exception
        assert "What the agent recommends" in _text(at)
        assert at.session_state["reviewer"].id == "human:owner"

    def test_run_locally_the_token_is_still_required(self, desk: dict[str, Any]) -> None:
        at = AppTest.from_function(_review, default_timeout=60).run()
        assert at.text_input[0].label == "Review console token"
        at.button[0].click().run()  # no token typed
        assert at.error and "not correct" in at.error[0].value
        assert "What the agent recommends" not in _text(at)


class TestAnApprovalIsActedOn:
    """The step the first ADR 0047 tests skipped: after the click, the engine must act.

    The engine accepts decisions only from `approvals.approver_ids` (T09, R13). Review once
    let a reviewer type any name, and a credit approved as "asha" was recorded and then not
    acted on - the case went to a person. Driven here through the real line to the ledger.
    """

    @pytest.fixture
    def world(self, monkeypatch: pytest.MonkeyPatch) -> Any:
        from dispute_agent.ui import data as console_data

        world = open_world(
            "calc_error_high_value",
            policy=PolicyConfig(),
            clock=FakeClock(NOW),
            ids=FakeIdGenerator(),
            decide=lambda _a: None,  # the decision comes from the Review screen
        )
        world.drive(20)
        monkeypatch.setattr(console_data, "CASES", world.folder / "cases.sqlite3")
        monkeypatch.setenv("REVIEW_CONSOLE_TOKEN", "t-" + secrets.token_hex(8))
        yield world
        world.close()

    def _state(self, world: Any) -> CaseState:
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            return work.cases.get(world.case_id).state

    def test_a_credit_approved_on_the_review_screen_reaches_the_ledger(self, world: Any) -> None:
        import os

        at = AppTest.from_function(_review, default_timeout=60).run()
        at.text_input[0].input(os.environ["REVIEW_CONSOLE_TOKEN"])
        at.button[0].click().run()
        next(b for b in at.button if b.label == "Approve").click().run()
        assert not at.exception

        world.drive(20)  # the runner picks the decision up, as the hosted service does
        posted = [e for e in world.ledger.get_entries("SHP-000002") if str(e.type) == "CREDIT"]
        assert [e.amount.amount for e in posted] == [Decimal("1820.00")]  # posted, read back
        # Next, the email telling the seller waits for its own approval (I13).
        assert self._state(world) is CaseState.AWAITING_APPROVAL

    def test_a_decision_by_an_unlisted_name_is_not_acted_on(self, world: Any) -> None:
        """Why Review offers no other name: the engine would set the decision aside."""
        from dispute_agent.modules.approvals.streamlit_ui.console import (
            ReviewConsole,
            reviewer_named,
        )

        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            console = ReviewConsole(
                store=work.approvals, clock=world.clock, reviewer=reviewer_named("asha")
            )
            console.approve(console.queue()[0].id, "")
        world.drive(20)
        assert self._state(world) is CaseState.NEEDS_HUMAN_REVIEW
        assert not [e for e in world.ledger.get_entries("SHP-000002") if str(e.type) == "CREDIT"]


@pytest.mark.parametrize("name", ["", "   ", "<b>x</b>", "a" * 41])
def test_an_odd_approver_name_is_refused(name: str) -> None:
    from dispute_agent.kernel.domain.errors import DomainValidationError
    from dispute_agent.modules.approvals.streamlit_ui.console import reviewer_named

    with pytest.raises(DomainValidationError):
        reviewer_named(name)
