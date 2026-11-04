"""The recurrence monitor (P7-09).

For every pattern with a fix on record: disputes on its key in the window before the fix and in
the same-length window after it. If, while the pattern is FIXED_MONITORING, the disputes after
the fix reach the cluster threshold again, the fix did not hold and the pattern goes back to
CONFIRMED - more caution, never less (I7). The counts are reported in the eval report.

Counts are of disputes opened, by code, from the cases table (read-only). They measure whether
disputes continued, not whether any of them was right.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.modules.cross_case.analyzer.clustering import Thresholds
    from dispute_agent.modules.cross_case.sql_context import ShipmentKeys
    from dispute_agent.modules.cross_case.sql_context.repository import (
        PatternRepository,
        StoredPattern,
    )


@dataclass(frozen=True)
class RecurrenceRow:
    pattern_id: str
    dimension: str
    value: str
    fixed_at: datetime
    before: int
    after: int
    status: str
    reopened: bool


@dataclass
class RecurrenceMonitor:
    repository: PatternRepository
    keys: ShipmentKeys
    thresholds: Thresholds

    def run(self, now: datetime) -> list[RecurrenceRow]:
        rows: list[RecurrenceRow] = []
        for pattern in self.repository.all():
            if pattern.fixed_at is None:
                continue
            window = timedelta(days=self.thresholds.window_days)
            fixed = pattern.fixed_at
            before = self._count(pattern, fixed - window, fixed - timedelta(microseconds=1))
            after = self._count(pattern, fixed, min(now, fixed + window))
            reopened = (
                pattern.status == "FIXED_MONITORING" and after >= self.thresholds.min_related_cases
            )
            status = pattern.status
            if reopened:
                self.repository.set_status(pattern.id, "CONFIRMED", "recurrence_after_fix", now)
                status = "CONFIRMED"
            rows.append(
                RecurrenceRow(
                    pattern.id,
                    pattern.dimension,
                    pattern.match_value,
                    fixed,
                    before,
                    after,
                    status,
                    reopened,
                )
            )
        return rows

    def _count(self, pattern: StoredPattern, start: datetime, end: datetime) -> int:
        count = 0
        for row in self.repository.cases_opened_between(start, end):
            if pattern.dimension == "seller":
                value = row.seller_ref
            else:
                try:
                    value = dict(self.keys(row.shipment_ref)).get(pattern.dimension, "")
                except (LookupError, ValueError, OSError):
                    continue
            if value == pattern.match_value:
                count += 1
        return count


def render(rows: list[RecurrenceRow]) -> str:
    """Markdown for the eval report. Counts carry their windows, never a percentage alone."""
    lines = [
        "## Recurrence after confirmed fixes (Phase 7)",
        "",
        "Disputes on each fixed cause, in equal windows before and after the fix.",
        "",
        "| Pattern | Cause | Fixed at | Before | After | Status |",
        "|---------|-------|----------|-------:|------:|--------|",
    ]
    if not rows:
        lines.append("| - | no fix on record | - | - | - | - |")
    for r in rows:
        lines.append(
            f"| {r.pattern_id} | {r.dimension} {r.value} | {r.fixed_at:%Y-%m-%d} | "
            f"{r.before} | {r.after} | {r.status}{' (reopened)' if r.reopened else ''} |"
        )
    return "\n".join(lines) + "\n"
