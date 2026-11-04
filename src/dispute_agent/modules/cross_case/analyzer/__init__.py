"""PatternAnalyzer: the scheduled job that finds clusters across disputes (P7-03).

It reads cases **read-only** and writes only the module's own pattern rows. It holds no write
port and no executor (`test_analyzer_never_writes.py`): opening the Jira investigation is a
separate step that goes through the kernel's policy engine and ActionExecutor (P7-05, I2).

A re-run is safe. A cluster seen again refreshes its pattern (summary, last seen, members);
its status is never changed here - only independent evidence moves a status (P7-08). A
dismissed pattern stays dismissed: ops found the cause legitimate, and seeing the same
legitimate cause again is not new evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from dispute_agent.modules.cross_case.analyzer.clustering import (
    Cluster,
    KeyedCase,
    Thresholds,
    find_clusters,
    summary_for,
)

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.ports import IdGenerator
    from dispute_agent.modules.cross_case.sql_context import ShipmentKeys
    from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository


@dataclass
class AnalysisReport:
    clusters: list[Cluster] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)
    #: Cases whose shipment keys could not be read. Reported, never guessed.
    unreadable: list[str] = field(default_factory=list)

    @property
    def pattern_ids(self) -> list[str]:
        return [*self.created, *self.refreshed]


@dataclass
class PatternAnalyzer:
    repository: PatternRepository
    keys: ShipmentKeys
    ids: IdGenerator
    thresholds: Thresholds = field(default_factory=Thresholds)

    def run(self, now: datetime) -> AnalysisReport:
        report = AnalysisReport()
        start = now - timedelta(days=self.thresholds.window_days)
        keyed: list[KeyedCase] = []
        for row in self.repository.cases_opened_between(start, now):
            try:
                keys = dict(self.keys(row.shipment_ref))
            except (LookupError, ValueError, OSError):
                report.unreadable.append(row.case_id)
                keys = {}
            keys["seller"] = row.seller_ref
            keyed.append(KeyedCase(row.case_id, row.opened_at, keys))

        report.clusters = find_clusters(keyed, self.thresholds, now)
        for cluster in report.clusters:
            existing = self.repository.find(cluster.dimension, cluster.value)
            if existing is None:
                pattern_id = self.ids.new_id("pat")
                self.repository.create(
                    pattern_id,
                    dimension=cluster.dimension,
                    value=cluster.value,
                    summary=summary_for(cluster),
                    thresholds=self.thresholds.as_dict(),
                    first_seen_at=cluster.first_seen_at,
                    last_seen_at=cluster.last_seen_at,
                    now=now,
                )
                report.created.append(pattern_id)
            else:
                pattern_id = existing.id
                self.repository.refresh(
                    pattern_id,
                    summary=summary_for(cluster),
                    last_seen_at=max(existing.last_seen_at, cluster.last_seen_at),
                    now=now,
                )
                report.refreshed.append(pattern_id)
            self.repository.link(pattern_id, cluster.case_ids, "member", now)
        return report


__all__ = ["AnalysisReport", "PatternAnalyzer", "Thresholds"]
