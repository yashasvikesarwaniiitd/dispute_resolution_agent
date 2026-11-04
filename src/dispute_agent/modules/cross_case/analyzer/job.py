"""CrossCaseJob: the scheduled analysis, one pass in a fixed order (P7-03 to P7-09).

1. **Analyze** (read-only over cases): find clusters, create or refresh patterns.
2. **Describe**: new patterns get competing hypotheses from the pattern analyst, if the model
   route is on; otherwise they keep the code-written summary.
3. **Investigate**: each open pattern without a verified issue gets one, through the kernel's
   policy engine and ActionExecutor (I2). This step is the only one that can write outside,
   and it cannot write except through the executor.
4. **Confirm**: ops findings on investigation issues move statuses (independent evidence only).
5. **Monitor**: recurrence after a fix; a fix that did not hold reopens the pattern.

The runner calls `run` when `due`; every step is idempotent, so a crash part-way through is
repaired by the next run. A failure here never touches a case: the runner records it and the
next tick carries on (cross-case learning sits downstream of every decision).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from dispute_agent.modules.cross_case.analyzer.hypotheses import ClusterStats

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.modules.cross_case.analyzer import AnalysisReport, PatternAnalyzer
    from dispute_agent.modules.cross_case.analyzer.confirmation import (
        ConfirmationRule,
        FindingsReader,
    )
    from dispute_agent.modules.cross_case.analyzer.hypotheses import PatternAnalyst
    from dispute_agent.modules.cross_case.analyzer.investigations import (
        InvestigationResult,
        Investigations,
    )
    from dispute_agent.modules.cross_case.analyzer.recurrence import (
        RecurrenceMonitor,
        RecurrenceRow,
    )

log = structlog.get_logger(__name__)

#: Patterns described per run: the analyst is a paid call, so it is bounded.
MAX_DESCRIBED_PER_RUN = 5


@dataclass
class JobReport:
    analysis: AnalysisReport | None = None
    described: list[str] = field(default_factory=list)
    investigations: list[InvestigationResult] = field(default_factory=list)
    confirmations: list[tuple[str, str, str]] = field(default_factory=list)
    recurrence: list[RecurrenceRow] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CrossCaseJob:
    analyzer: PatternAnalyzer
    investigations: Investigations
    confirmation: ConfirmationRule
    findings: FindingsReader
    recurrence: RecurrenceMonitor
    analyst: PatternAnalyst | None = None
    every: timedelta = timedelta(hours=1)
    name: str = "cross_case"
    _last_run: datetime | None = None
    _findings_since: datetime | None = None

    def due(self, now: datetime) -> bool:
        return self._last_run is None or now - self._last_run >= self.every

    def run(self, now: datetime) -> JobReport:
        report = JobReport()
        report.analysis = self.analyzer.run(now)
        for pattern_id in report.analysis.created[:MAX_DESCRIBED_PER_RUN]:
            if self._describe(pattern_id, now):
                report.described.append(pattern_id)
        repository = self.analyzer.repository
        for pattern in repository.all():
            if pattern.status != "DISMISSED":
                try:
                    report.investigations.append(self.investigations.open(pattern.id, now))
                except Exception as failed:  # one pattern's trouble never stops the next
                    report.errors.append(f"investigate:{pattern.id}:{type(failed).__name__}")
        # Overlap the last read by a day: a finding stamped at the instant of the last run
        # would otherwise fall between two reads. Applying one twice is a no-op.
        since = (self._findings_since or now - timedelta(days=365)) - timedelta(days=1)
        try:
            report.confirmations = self.findings.run(since, now)
            self._findings_since = now
        except Exception as failed:  # e.g. a ticketing adapter that cannot list findings
            report.errors.append(f"findings:{type(failed).__name__}")
        try:
            report.recurrence = self.recurrence.run(now)
        except Exception as failed:
            report.errors.append(f"recurrence:{type(failed).__name__}")
        self._last_run = now
        if report.errors:
            log.error("cross_case_job_partial", errors=report.errors)
        return report

    def _describe(self, pattern_id: str, now: datetime) -> bool:
        if self.analyst is None:
            return False
        repository = self.analyzer.repository
        pattern = repository.get(pattern_id)
        informed = set(repository.linked(pattern_id, "informed"))
        members = [c for c in repository.linked(pattern_id, "member") if c not in informed]
        stats = ClusterStats(
            dimension=pattern.dimension,
            value=pattern.match_value,
            cases=len(members),
            window_days=pattern.thresholds.get("window_days", 14),
            min_related_cases=pattern.thresholds.get("min_related_cases", 3),
            case_ids=tuple(members),
            pattern_assisted_excluded=len(informed),
        )
        prior = [
            {"id": p.id, "status": p.status, "source": p.status_reason}
            for p in repository.all()
            if p.status in ("CONFIRMED", "DISMISSED", "FIXED_MONITORING") and p.id != pattern_id
        ]
        described = self.analyst.describe(
            stats, prior_findings=prior, case_notes=repository.case_notes(members)
        )
        if described is None:
            return False
        repository.set_hypotheses(
            pattern_id,
            [
                *(dict(h) for h in described.hypotheses),
                {
                    "investigation_request": described.investigation_request,
                    "evidence_strength": described.evidence_strength,
                    "model_id": described.model_id,
                    "prompt_hash": described.prompt_hash,
                },
            ],
            now,
        )
        repository.set_summary(pattern_id, described.summary, "model", now)
        return True
