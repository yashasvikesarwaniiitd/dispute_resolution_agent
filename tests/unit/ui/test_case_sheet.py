"""ADR 0050: the review sheet tells a person what to decide, with the facts and code's choices."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import InternalEventType, ReviewCode
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.ui import case_sheet

if TYPE_CHECKING:
    from collections.abc import Iterator

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)


def _sheet(scenario: str) -> case_sheet.Sheet:
    world = open_world(
        scenario,
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        decide=lambda _a: None,
    )
    try:
        world.drive(40)
        with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
            return case_sheet.build(work, work.cases.get(world.case_id))
    finally:
        world.close()


@pytest.fixture(scope="module")
def photo_case() -> Iterator[case_sheet.Sheet]:
    yield _sheet("kurta_box_larger")


def test_a_photo_case_asks_whose_measurement_to_trust(photo_case: case_sheet.Sheet) -> None:
    assert photo_case.task.startswith("Decide whose measurement to trust")
    assert any("ask the hub to remeasure" in c for c in photo_case.checks)


def test_the_facts_put_the_declared_and_measured_size_side_by_side(
    photo_case: case_sheet.Sheet,
) -> None:
    facts = dict(photo_case.facts)
    x = case_sheet.TIMES
    assert facts["Declared by the seller"] == f"20 {x} 15 {x} 10 cm · 480 g"
    assert facts["Measured by the hub"].startswith(f"30 {x} 22 {x} 15 cm · 1,100 g")
    figures = dict(photo_case.figures)
    assert figures["Charged"] == "₹152.00" and figures["Difference"] == "₹0.00"
    assert figures["Errors code proved"] == "none"
    # What is at stake: the same card on the seller's declared size (display only).
    assert figures["Rate card on the seller's declared size"] == "₹76.00"
    assert photo_case.claim == "My kurta weighs 480 g, why am I charged for 2 kg?"


def test_no_credit_is_offered_where_code_proved_no_error(photo_case: case_sheet.Sheet) -> None:
    assert [v.action for v in photo_case.verdicts] == [
        "uphold_charge",
        "request_remeasurement",
        "request_seller_evidence",
    ]


def test_two_proven_errors_offer_one_credit_each_at_codes_amount() -> None:
    sheet = _sheet("multi_error")
    assert sheet.task.startswith("Code proved two errors")
    offered = [(v.reason_code, v.label) for v in sheet.verdicts if v.action == "propose_credit"]
    assert offered == [
        ("calculation_error", "Credit the seller ₹1,820.00 for the calculation error"),
        ("duplicate_charge", "Credit the seller ₹2,800.00 for the duplicate charge"),
    ]


def test_a_manipulation_case_says_to_read_the_sellers_text_as_data() -> None:
    task, checks = case_sheet.task_for(ReviewCode.MANIPULATION_SUSPECTED, "R21")
    assert "instructions aimed at the AI" in task
    assert any("as data" in c for c in checks)


def test_an_unknown_reason_still_gets_a_task() -> None:
    task, checks = case_sheet.task_for(ReviewCode.INTERNAL_ERROR, "x")
    assert task and checks


def test_an_ineligible_assessment_offers_no_verdict() -> None:
    assessment = SimpleNamespace(eligible="unknown", definite_errors=())
    offered, why = case_sheet.verdicts(assessment, None)  # type: ignore[arg-type]
    assert offered == () and "eligible" in why


def test_a_refusal_shows_until_the_case_moves() -> None:
    refused = SimpleNamespace(
        type=InternalEventType.HUMAN_DECISION_REFUSED, payload=SimpleNamespace(detail="R14: no")
    )
    moved = SimpleNamespace(type=InternalEventType.STATE_CHANGED, payload=None)
    assert case_sheet.last_refusal([refused]) == "R14: no"
    assert case_sheet.last_refusal([refused, moved]) == ""


def test_the_sellers_words_are_escaped_never_markup() -> None:
    sheet = case_sheet.Sheet(
        task="t",
        checks=(),
        claim="<script>x</script>",
        facts=(("a", "<b>"),),
        figures=(),
        verdicts=(),
    )
    shown = case_sheet.facts_html(sheet)
    assert "<script>" not in shown and "&lt;script&gt;" in shown
    assert "<dd><b></dd>" not in shown
