"""The runner: timers, the inbox, stuck cases and outcomes (orchestrator.md sections 6 and 7).

Timers are rows; nothing sleeps in memory. `tick()` is what a background loop calls every few
seconds and what a test calls directly with a fake clock. `recover()` runs once at start-up,
before anything else, and is safe to run twice: every step in it is idempotent.

Every unit of work here is its own transaction. One event that fails does not roll back
another, and a crash between two of them loses at most the one in flight - which is safe to
redo, because external writes carry idempotency keys and dispatch appends an input event once.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.exc import OperationalError

from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ActorKind, CaseState, InboundEventType, ReviewCode
from dispute_agent.kernel.domain.errors import ConcurrencyConflict, DuplicateEvent, NotFound
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.ids import CaseId, InboxId
from dispute_agent.kernel.domain.inbound import TimerFired
from dispute_agent.kernel.domain.models import Actor, ReviewReason
from dispute_agent.kernel.orchestrator.orchestrator import AUTO_STATES

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from dispute_agent.api.line import Line
    from dispute_agent.kernel.orchestrator.dispatch import DispatchResult

log = structlog.get_logger(__name__)

SCHEDULER = Actor(kind=ActorKind.SYSTEM, id="scheduler")

#: States a failed event may send to a person from (transitions.yaml `escalable`).
ESCALABLE = frozenset(
    {
        CaseState.RECEIVED,
        CaseState.GATHERING_EVIDENCE,
        CaseState.CALCULATING,
        CaseState.INVESTIGATING,
        CaseState.EXECUTING,
        CaseState.VERIFYING,
        CaseState.AWAITING_SELLER,
        CaseState.AWAITING_OPS,
        CaseState.AWAITING_APPROVAL,
    }
)

#: Recovery order (section 7): in-flight actions first, then the rest of the automatic work.
IN_FLIGHT_STATES = (CaseState.EXECUTING, CaseState.VERIFYING)


@dataclass
class TickReport:
    timers_fired: int = 0
    dispatched: list[DispatchResult] = field(default_factory=list)
    advanced: int = 0
    outcomes_delivered: int = 0
    jobs_run: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


@dataclass
class Runner:
    engine: Engine
    line: Line
    #: HOOK C: who outcomes are delivered to. The Null observer until Phase 7.
    observer: object | None = None
    #: Scheduled jobs (the cross-case analysis, P7-03): `due(now)` and `run(now)`. Run after
    #: outcomes are delivered, so a job sees every case that has closed.
    jobs: list[Any] = field(default_factory=list)

    def _work(self) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(self.engine, clock=self.line.clock)

    # -- inbound -------------------------------------------------------------------------------

    def submit(self, event: InboundEvent) -> DispatchResult:
        """Accept an inbound event (a reply, a decision, a finding) and dispatch it.

        A repeat of the same source event is answered with the first one's result.
        """
        with self._work() as work:
            try:
                inbox_id = work.inbox.accept(event, self.line.clock.now())
            except DuplicateEvent as duplicate:
                inbox_id = duplicate.existing_inbox_id
        # Accepted is accepted: if handling it loses a race or fails, the event is already
        # safe in the inbox and the same rules as the tick apply - retry, or a person.
        return self._safe_dispatch(inbox_id, TickReport())

    def dispatch(self, inbox_id: InboxId) -> DispatchResult:
        with self._work() as work:
            return self.line.dispatcher(work).dispatch(inbox_id)

    # -- the tick ------------------------------------------------------------------------------

    def tick(self) -> TickReport:
        report = TickReport()
        self.fire_due(report)
        self.dispatch_pending(report)
        self.advance_stuck(report)
        self.deliver_outcomes(report)
        self.run_jobs(report)
        return report

    def fire_due(self, report: TickReport | None = None) -> TickReport:
        """Mark due timers fired and put a TIMER_FIRED in the inbox, in one transaction each.

        The dedupe key `scheduler:<timer_id>` means a timer fires at most once even if two
        runners race. A timer that fires late still fires once, carrying its original due_at.
        """
        report = report or TickReport()
        now = self.line.clock.now()
        with self._work() as work:
            due = list(work.timers.due(now))
        for timer in due:
            with self._work() as work:
                try:
                    work.timers.mark_fired(timer.id, now)
                except NotFound:  # already fired or cancelled by a racing runner
                    continue
                try:
                    inbox_id = work.inbox.accept(
                        InboundEvent(
                            type=InboundEventType.TIMER_FIRED,
                            source="scheduler",
                            source_event_id=str(timer.id),
                            occurred_at=now,
                            actor=SCHEDULER,
                            case_id=timer.case_id,
                            correlation_id=timer.correlation_id,
                            payload=TimerFired(
                                timer_id=timer.id, kind=timer.kind, due_at=timer.due_at
                            ),
                        ),
                        now,
                    )
                except DuplicateEvent as duplicate:
                    inbox_id = duplicate.existing_inbox_id
            report.timers_fired += 1
            report.dispatched.append(self._safe_dispatch(inbox_id, report))
        return report

    def dispatch_pending(self, report: TickReport | None = None) -> TickReport:
        report = report or TickReport()
        with self._work() as work:
            pending = work.inbox.pending()
        for inbox_id in pending:
            report.dispatched.append(self._safe_dispatch(InboxId(inbox_id), report))
        return report

    def advance_stuck(
        self,
        report: TickReport | None = None,
        states: tuple[CaseState, ...] = AUTO_STATES,
    ) -> TickReport:
        """Cases in an automatic state that nothing is moving (a verify waiting, a crash)."""
        report = report or TickReport()
        with self._work() as work:
            cases = [c.id for c in work.cases.list_in_states(states)]
        for case_id in cases:
            try:
                with self._work() as work:
                    case = work.cases.get(case_id)
                    if case.state not in states:
                        continue
                    history = work.events.list_for_case(case.id)
                    if not history:
                        continue
                    self.line.advance(work, case, history[-1].id)
                report.advanced += 1
            except ConcurrencyConflict:
                continue  # someone else moved it; the next tick looks again
            except Exception as failed:  # a bug in a step must not stop the runner
                # The type only: an exception's text can quote a seller's words (I5, I12).
                log.error("advance_failed", case_id=str(case_id), error=type(failed).__name__)
                report.failures.append(f"advance {case_id}: {type(failed).__name__}")
        return report

    def deliver_outcomes(self, report: TickReport | None = None) -> TickReport:
        """HOOK C: hand finished cases to the observer, outside the case's transaction."""
        report = report or TickReport()
        if self.observer is None:
            return report
        with self._work() as work:
            pending = list(work.outcomes.undelivered())
        # The observer is called with no transaction open here. Holding the write lock while
        # it writes its own rows (P7-07) would make it wait on this runner and time out, so
        # every outcome would fail and none would ever be learned from.
        for outcome in pending:
            failure: str | None = None
            try:
                self.observer.on_outcome(outcome)  # type: ignore[attr-defined]
            except Exception as failed:  # the observer's failure never affects a case
                failure = type(failed).__name__
            with self._work() as work:
                if failure is None:
                    work.outcomes.mark_delivered(str(outcome.id), self.line.clock.now())
                    report.outcomes_delivered += 1
                else:
                    work.outcomes.mark_failed(str(outcome.id), failure)
        return report

    def run_jobs(self, report: TickReport | None = None) -> TickReport:
        """Run every due job. A failing job is recorded and never reaches a case."""
        report = report or TickReport()
        now = self.line.clock.now()
        for job in self.jobs:
            if not job.due(now):
                continue
            try:
                job.run(now)
                report.jobs_run.append(str(job.name))
            except Exception as failed:  # downstream of every decision; the next tick retries
                log.error("job_failed", job=str(job.name), error=type(failed).__name__)
                report.failures.append(f"job:{job.name}:{type(failed).__name__}")
        return report

    # -- recovery ------------------------------------------------------------------------------

    def recover(self) -> TickReport:
        """Start-up recovery (orchestrator.md section 7), in the spec's order."""
        report = TickReport()
        self.advance_stuck(report, IN_FLIGHT_STATES)  # in-flight actions first
        self.advance_stuck(report)
        self.dispatch_pending(report)
        self._redeliver_all(report)
        self.fire_due(report)  # missed timers fire now, once
        self.deliver_outcomes(report)
        log.info(
            "recovery_complete",
            timers_fired=report.timers_fired,
            dispatched=len(report.dispatched),
            advanced=report.advanced,
            failures=len(report.failures),
        )
        return report

    def _redeliver_all(self, report: TickReport) -> None:
        with self._work() as work:
            open_states = [s for s in CaseState if s not in (CaseState.HANDED_TO_HUMAN,)]
            case_ids = [c.id for c in work.cases.list_in_states(open_states)]
        for case_id in case_ids:
            with self._work() as work:
                if work.inbox.deferred_for_case(case_id):
                    self.line.dispatcher(work).redeliver(case_id)

    def _safe_dispatch(self, inbox_id: InboxId, report: TickReport) -> DispatchResult:
        """Dispatch one event in its own transaction, and never lose it (safety review, M5).

        A transient failure (a concurrent writer, a locked database) leaves the row PENDING,
        so the next tick tries again. Anything else marks it FAILED *and* sends its case to a
        person: a fired deadline whose event failed would otherwise never fire again, and the
        case would wait forever with nobody told (I4).
        """
        from dispute_agent.kernel.orchestrator.dispatch import DispatchResult as _Result

        try:
            return self.dispatch(inbox_id)
        except (ConcurrencyConflict, OperationalError) as transient:
            log.warning("dispatch_retry", inbox_id=str(inbox_id), error=type(transient).__name__)
            return _Result(inbox_id, None, f"retry:{type(transient).__name__}")
        except Exception as failed:
            error = type(failed).__name__
            log.error("dispatch_failed", inbox_id=str(inbox_id), error=error)
            report.failures.append(f"dispatch {inbox_id}: {error}")
            case_id = self._fail(inbox_id, error)
            return _Result(inbox_id, case_id, f"failed:{error}")

    def _fail(self, inbox_id: InboxId, error: str) -> CaseId | None:
        """Mark the row FAILED and hand its case to a person, in one transaction."""
        with self._work() as work:
            work.inbox.mark_failed(inbox_id, error)
            row = work.inbox.get(inbox_id)
            case_id = CaseId(str(row["case_id"])) if row.get("case_id") else None
            if case_id is None:
                return None  # no case known: the FAILED row is what a person looks at
            case = work.cases.get(case_id)
            if case.state not in ESCALABLE:
                return case_id
            history = work.events.list_for_case(case_id)
            if not history:
                return case_id
            self.line.orchestrator(work).escalate(
                case,
                ReviewReason(
                    code=ReviewCode.INTERNAL_ERROR,
                    detail=f"an inbound {row['event_type']} could not be processed: {error}",
                ),
                history[-1].id,
                commit=lambda before, after: work.cases.save(after, before.version),
            )
            return case_id


__all__ = ["Runner", "TickReport"]
