"""What the routes are allowed to do (P1-11).

A thin seam between HTTP and the kernel. The routes know about status codes and JSON; this
knows about units of work and steps; neither knows about the other's concerns. Keeping them
apart is what stops an endpoint growing a second, slightly different version of intake.

Every public method here is one transaction. A request either changes the case and records
why, or changes nothing at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from dispute_agent.api.events import SubmitResult, input_event
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.kernel.domain.errors import DuplicateEvent
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from dispute_agent.api.events import RaiseDispute
    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.events import Event, InboundEvent
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.orchestrator.intake import Intake
    from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator
    from dispute_agent.kernel.ports import Clock, IdGenerator

log = structlog.get_logger()


class Runtime:
    """Holds the wiring one request needs. Built once, by the composition root."""

    def __init__(
        self,
        unit_of_work: Callable[[], SqliteUnitOfWork],
        intake: Intake,
        orchestrator: Orchestrator,
        clock: Clock,
        ids: IdGenerator,
        *,
        pack: str = "weight_dispute",
    ) -> None:
        self._uow = unit_of_work
        self._intake = intake
        self._orchestrator = orchestrator
        self._clock = clock
        self._ids = ids
        self._pack = pack

    def submit_dispute(self, event: InboundEvent, body: RaiseDispute) -> SubmitResult:
        """Accept, route, advance — all in one transaction (ADR 0004, ADR 0006)."""
        from dispute_agent.kernel.domain.case import CaseFacts

        facts = CaseFacts(
            shipment_ref=body.shipment_ref,
            claim_ref=body.claim_ref,
            seller_ref=body.seller_ref,
            seller_contact=body.seller_contact,
            raised_at=body.raised_at,
        )
        # Checked before anything is written, so a refusal leaves no trace (I11, S27).
        self._intake.check(facts)

        now = self._clock.now()
        with self._uow() as work:
            inbox = SqliteInboxStore(work.connection)
            try:
                inbox_id = inbox.accept(event, now)
            except DuplicateEvent as duplicate:
                existing = inbox.get(duplicate.existing_inbox_id)
                log.info(
                    "event_duplicate",
                    case_id=existing["case_id"],
                    correlation_id="corr_none",
                )
                return SubmitResult(
                    inbox_id=str(duplicate.existing_inbox_id),
                    duplicate=True,
                    case_id=existing["case_id"],
                    state=self._state_of(work, existing["case_id"]),
                )

            result = self._intake.open_or_get_case(
                work.cases,
                event,
                facts,
                pack=self._pack,
                now=now,
                case_id=CaseId(self._ids.new_id("case")),
                correlation_id=CorrelationId(self._ids.new_id("corr")),
                opened_event_id=EventId(self._ids.new_id("evt")),
                causation_id=EventId(self._ids.new_id("evt")),
            )
            if result.created:
                work.cases.insert(result.case)

            raised = work.events.append(input_event(str(result.case.id), event, str(inbox_id)))
            for opened in result.events:
                work.events.append(opened.model_copy(update={"causation_id": raised.id}))

            advanced = self._orchestrator.advance(result.case, raised.id)
            for produced in advanced.events:
                work.events.append(produced)
            # Compared whole, not by state. A transition whose only effects are a
            # counter bump or clear_pending would otherwise commit its STATE_CHANGED
            # event and discard the row change, leaving the diary and the case row
            # disagreeing - the exact thing I8 exists to prevent. Not reachable in
            # Phase 1, reachable in Phase 2. Found by the Phase 1 safety review.
            if advanced.case != result.case:
                work.cases.save(advanced.case, result.case.version)

            inbox.mark_processed(inbox_id, result.case.id, raised.id)

            log.info(
                "dispute_submitted",
                case_id=str(advanced.case.id),
                correlation_id=str(advanced.case.correlation_id),
                state=advanced.case.state.value,
                steps=advanced.steps_run,
            )
            return SubmitResult(
                inbox_id=str(inbox_id),
                duplicate=False,
                case_id=str(advanced.case.id),
                state=advanced.case.state.value,
                review_reason=(
                    None
                    if advanced.case.review_reason is None
                    else advanced.case.review_reason.render()
                ),
            )

    def read_case(self, case_id: str) -> tuple[Case, Sequence[Event]]:
        with self._uow() as work:
            case = work.cases.get(CaseId(case_id))
            events = work.events.list_for_case(CaseId(case_id))
        return case, events

    def _state_of(self, work: SqliteUnitOfWork, case_id: object) -> str | None:
        if case_id is None:
            return None
        return work.cases.get(CaseId(str(case_id))).state.value
