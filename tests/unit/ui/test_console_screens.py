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
    ):
        assert expected in text


def test_a_reviewer_signs_in_sees_the_recommendation_and_approves(desk: dict[str, Any]) -> None:
    at = AppTest.from_function(_review, default_timeout=60).run()
    assert not at.exception
    at.text_input[0].input(desk["token"])
    at.text_input[1].input("asha")
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
        (ApprovalDecision.APPROVED, "human:asha")
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
    assert values["Credited back"] == "INR 0.00"  # nothing approved yet
    assert values["Charged"].startswith("INR ")


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
    at.text_input[1].input("asha")
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
    assert len(rows) == 1 and rows[0][1] == "ops_console" and "human:asha" in rows[0][2]
