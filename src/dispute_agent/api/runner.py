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
from typing import TYPE_CHECKING

import structlog

from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ActorKind, CaseState, InboundEventType
from dispute_agent.kernel.domain.errors import ConcurrencyConflict, DuplicateEvent
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.ids import InboxId
from dispute_agent.kernel.domain.inbound import TimerFired
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.orchestrator.orchestrator import AUTO_STATES

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from dispute_agent.api.line import Line
    from dispute_agent.kernel.orchestrator.dispatch import DispatchResult

log = structlog.get_logger(__name__)

SCHEDULER = Actor(kind=ActorKind.SYSTEM, id="scheduler")

#: Recovery order (section 7): in-flight actions first, then the rest of the automatic work.
IN_FLIGHT_STATES = (CaseState.EXECUTING, CaseState.VERIFYING)


@dataclass
class TickReport:
    timers_fired: int = 0
    dispatched: list[DispatchResult] = field(default_factory=list)
    advanced: int = 0
    outcomes_delivered: int = 0
    failures: list[str] = field(default_factory=list)


@dataclass
class Runner:
    engine: Engine
    line: Line
    #: HOOK C: who outcomes are delivered to. The Null observer until Phase 7.
    observer: object | None = None

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
        return self.dispatch(inbox_id)

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
                except Exception:  # already fired or cancelled by a racing runner
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
                log.exception("advance_failed", case_id=str(case_id))
                report.failures.append(f"advance {case_id}: {type(failed).__name__}")
        return report

    def deliver_outcomes(self, report: TickReport | None = None) -> TickReport:
        """HOOK C: hand finished cases to the observer, outside the case's transaction."""
        report = report or TickReport()
        if self.observer is None:
            return report
        with self._work() as work:
            for outcome in work.outcomes.undelivered():
                try:
                    self.observer.on_outcome(outcome)  # type: ignore[attr-defined]
                    work.outcomes.mark_delivered(str(outcome.id), self.line.clock.now())
                    report.outcomes_delivered += 1
                except Exception as failed:  # the observer's failure never affects a case
                    work.outcomes.mark_failed(str(outcome.id), type(failed).__name__)
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
                rows = work.inbox.deferred_for_case(case_id)
            if rows:
                report.dispatched.append(self._safe_dispatch(InboxId(str(rows[0]["id"])), report))

    def _safe_dispatch(self, inbox_id: InboxId, report: TickReport) -> DispatchResult:
        from dispute_agent.kernel.orchestrator.dispatch import DispatchResult as _Result

        try:
            return self.dispatch(inbox_id)
        except ConcurrencyConflict:
            return _Result(inbox_id, None, "retry:concurrency_conflict")
        except Exception as failed:
            # Recorded on the row and in the report; the row stays for a person (never lost).
            log.exception("dispatch_failed", inbox_id=str(inbox_id))
            with self._work() as work:
                work.inbox.mark_failed(inbox_id, type(failed).__name__)
            report.failures.append(f"dispatch {inbox_id}: {type(failed).__name__}")
            return _Result(inbox_id, None, f"failed:{type(failed).__name__}")


__all__ = ["Runner", "TickReport"]
