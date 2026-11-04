"""P7-09: disputes per cause before and after a fix; recurrence reopens the pattern."""

from __future__ import annotations

from datetime import timedelta

from dispute_agent.modules.cross_case.analyzer import Thresholds
from dispute_agent.modules.cross_case.analyzer.recurrence import RecurrenceMonitor, render
from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository
from tests.support.patterns import T0, add_case, add_pattern, new_engine

DAY = timedelta(days=1)
FIX = T0 + 10 * DAY


def world(after: int) -> tuple[RecurrenceMonitor, PatternRepository]:
    """Five disputes through the scanner before the fix, `after` disputes after it."""
    engine = new_engine()
    before = [f"case_b{n}" for n in range(5)]
    for n, case_id in enumerate(before):
        add_case(engine, case_id, opened_at=FIX - (n + 1) * DAY)
    for n in range(after):
        add_case(engine, f"case_a{n}", opened_at=FIX + (n + 1) * DAY)
    add_case(engine, "case_other", shipment="SHP-elsewhere", opened_at=FIX + DAY)
    repo = PatternRepository(engine)
    add_pattern(repo, "pat_1", "scanner", "SCN-03-2", before, seen=FIX - DAY)
    repo.set_status("pat_1", "FIXED_MONITORING", "ops:fix_deployed", FIX, fixed_at=FIX)

    def keys(shipment_ref: str) -> dict[str, str]:
        return {"scanner": "SCN-09-9" if shipment_ref == "SHP-elsewhere" else "SCN-03-2"}

    return RecurrenceMonitor(repo, keys, Thresholds()), repo


def test_a_fix_that_held_shows_the_drop() -> None:
    monitor, repo = world(after=1)
    [row] = monitor.run(FIX + 14 * DAY)
    assert (row.before, row.after, row.reopened) == (5, 1, False)
    assert repo.get("pat_1").status == "FIXED_MONITORING"


def test_recurrence_after_a_fix_reopens_the_pattern() -> None:
    monitor, repo = world(after=3)
    [row] = monitor.run(FIX + 14 * DAY)
    assert (row.before, row.after, row.reopened) == (5, 3, True)
    assert repo.get("pat_1").status == "CONFIRMED"
    assert repo.get("pat_1").status_reason == "recurrence_after_fix"


def test_the_report_carries_the_counts() -> None:
    monitor, _ = world(after=1)
    text = render(monitor.run(FIX + 14 * DAY))
    assert "| pat_1 | scanner SCN-03-2 |" in text
    assert "| 5 | 1 | FIXED_MONITORING |" in text


def test_a_pattern_without_a_fix_is_not_monitored() -> None:
    engine = new_engine()
    add_case(engine, "case_1")
    repo = PatternRepository(engine)
    add_pattern(repo, "pat_2", "hub", "HUB-01", ["case_1"])
    monitor = RecurrenceMonitor(repo, lambda ref: {"hub": "HUB-01"}, Thresholds())
    assert monitor.run(T0 + DAY) == []
