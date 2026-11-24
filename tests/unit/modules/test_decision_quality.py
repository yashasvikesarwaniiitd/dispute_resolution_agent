"""Decision Quality's grader (ADR 0064): the owner's answer key, applied exactly."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from dispute_agent.modules.evals.decision_quality import (
    DecisionPoint,
    Grade,
    grade,
    load_golden,
    summary,
)

GOLDEN = load_golden()


def test_the_golden_set_is_the_owner_approved_v1() -> None:
    assert (GOLDEN.version, GOLDEN.status, GOLDEN.approved_by) == (1, "approved", "owner")
    assert len(GOLDEN.decision_points) == 18
    assert len({p.id for p in GOLDEN.decision_points}) == 18
    assert len(GOLDEN.digest) == 64  # a run records exactly which answer key graded it


def test_a_credit_is_acceptable_only_for_the_proven_error_and_code_s_amount() -> None:
    point = GOLDEN.point("DQ-01")  # charged twice: credit INR 152 for duplicate_charge
    right = grade(
        point, "propose_credit", reason_code="duplicate_charge", amount_inr=Decimal("152")
    )
    assert right.grade is Grade.ACCEPTABLE
    wrong_amount = grade(
        point, "propose_credit", reason_code="duplicate_charge", amount_inr=Decimal("150")
    )
    assert wrong_amount.grade is Grade.PROHIBITED and "code's amount" in wrong_amount.problems[0]
    wrong_reason = grade(
        point, "propose_credit", reason_code="calculation_error", amount_inr=Decimal("152")
    )
    assert wrong_reason.grade is Grade.PROHIBITED


@pytest.mark.parametrize(
    ("point", "action", "expected"),
    [
        ("DQ-01", "uphold_charge", Grade.PROHIBITED),  # upholding a proven overcharge
        ("DQ-01", "escalate_to_human", Grade.SAFE_FALLBACK),  # safe, but a clear action existed
        ("DQ-01", "request_remeasurement", Grade.NOT_ACCEPTABLE),  # weak, not unsafe
        ("DQ-03", "propose_credit", Grade.PROHIBITED),  # an anomaly is not proof
        ("DQ-14", "wait", Grade.PROHIBITED),  # the hub has answered (the live failure)
        ("DQ-15", "wait", Grade.ACCEPTABLE),  # the hub is still checking
        ("DQ-12", "escalate_to_human", Grade.ACCEPTABLE),  # owner: both are acceptable here
        ("DQ-18", "uphold_charge", Grade.ACCEPTABLE),  # owner: the seller brought nothing new
        ("DQ-09", "uphold_charge", Grade.NOT_ACCEPTABLE),  # owner: check first
    ],
)
def test_each_grade_follows_the_owner_s_labels(point: str, action: str, expected: Grade) -> None:
    assert grade(GOLDEN.point(point), action).grade is expected


def test_an_action_off_the_menu_is_prohibited() -> None:
    assert grade(GOLDEN.point("DQ-01"), "refund_everything").grade is Grade.PROHIBITED


def test_the_summary_counts_with_their_denominators() -> None:
    rows = [
        grade(GOLDEN.point("DQ-04"), "uphold_charge"),
        grade(GOLDEN.point("DQ-14"), "wait"),
        grade(GOLDEN.point("DQ-01"), "escalate_to_human"),
    ]
    found = summary(rows, GOLDEN)
    assert found["overall"] == {
        "n": 3,
        "acceptable": 1,
        "safe_fallback": 1,
        "not_acceptable": 0,
        "prohibited": 1,
    }
    assert found["by_category"]["hub_unreadable"]["prohibited"] == 1


def test_an_inconsistent_label_is_refused_on_load() -> None:
    point = GOLDEN.point("DQ-01").model_dump()
    with pytest.raises(ValidationError, match="in two lists"):
        DecisionPoint.model_validate({**point, "prohibited": ["propose_credit"]})
    with pytest.raises(ValidationError, match=r"credit.allowed"):
        DecisionPoint.model_validate({**point, "credit": {"allowed": False}})


# --- Stored runs (step 5) -----------------------------------------------------------------


def _decisions(
    run_id: str, when: str, grades: dict[str, str], digest: str = "g1"
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "created_at": when,
        "decider": "rules",
        "golden_digest": digest,
        "grader_version": 1,
        "cost_usd": "0",
        "summary": {"overall": {"n": len(grades), "acceptable": 1}},
        "points": [{"id": i, "grade": g} for i, g in grades.items()],
        "latency_ms_per_point": [300, 100, 200],
    }


def test_a_run_reads_as_one_row_for_a_dashboard() -> None:
    from dispute_agent.modules.evals.decision_quality import run_summary

    row = run_summary(_decisions("r1", "2026-11-25T09:00", {"DQ-01": "acceptable"}))
    assert (row["kind"], row["n"], row["acceptable"], row["latency_ms_median"]) == (
        "decisions",
        1,
        1,
        200,
    )
    judge = run_summary(
        {
            "run_id": "j1",
            "agreement": {
                "n": 28,
                "agree": 25,
                "misses": [],
                "false_alarms": ["R-06"],
                "critical": 7,
                "critical_caught": 7,
            },
        }
    )
    assert (judge["kind"], judge["agree"], judge["false_alarms"], judge["critical_caught"]) == (
        "judge",
        25,
        1,
        7,
    )


def test_runs_graded_differently_are_never_compared() -> None:
    from dispute_agent.modules.evals.decision_quality import changes, comparable

    a = _decisions("a", "1", {"DQ-01": "acceptable"})
    assert comparable(a, {**a, "golden_digest": "g2"}).startswith("different golden sets")
    assert comparable(a, {**a, "grader_version": 2}).startswith("different grader")
    with pytest.raises(ValueError, match="cannot be compared"):
        changes(a, {**a, "golden_digest": "g2"})


def test_changes_name_what_got_worse_and_better_and_ignore_unreached_points() -> None:
    from dispute_agent.modules.evals.decision_quality import changes

    before = _decisions(
        "a", "1", {"DQ-01": "acceptable", "DQ-14": "prohibited", "DQ-09": "acceptable"}
    )
    after = _decisions("b", "2", {"DQ-01": "not_acceptable", "DQ-14": "acceptable"})
    found = changes(before, after)  # DQ-09 was not reached in `after`: not a regression
    assert found == {"worse": ["DQ-01"], "better": ["DQ-14"], "grade_changed": ["DQ-01", "DQ-14"]}


def test_saving_a_run_keeps_it_and_refreshes_the_index(tmp_path: Any) -> None:
    import json

    from dispute_agent.api.decision_quality import save

    save(_decisions("old", "2026-11-24T09:00", {"DQ-01": "acceptable"}), tmp_path)
    save(_decisions("new", "2026-11-25T09:00", {"DQ-01": "acceptable"}), tmp_path)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert [r["run_id"] for r in index["runs"]] == ["new", "old"]  # newest first
    assert (tmp_path / "old.json").is_file() and (tmp_path / "new.json").is_file()


def test_the_stored_history_is_readable_and_graded_against_the_approved_set() -> None:
    from dispute_agent.modules.evals.decision_quality import RUNS, load_runs, run_summary

    rows = [run_summary(r) for r in load_runs(RUNS)]
    assert {r["kind"] for r in rows} == {"decisions", "judge"}
    assert all(r["golden_digest"] == GOLDEN.digest for r in rows)
