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
from dispute_agent.kernel.domain.enums import InboundEventType
from dispute_agent.kernel.domain.errors import DuplicateEvent
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.inbound import HUMAN_EVENTS

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from dispute_agent.api.events import RaiseDispute
    from dispute_agent.api.line import Line
    from dispute_agent.api.runner import Runner
    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.events import Event, InboundEvent
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.orchestrator.dispatch import DispatchResult
    from dispute_agent.kernel.orchestrator.intake import Intake
    from dispute_agent.kernel.ports import Clock, IdGenerator

log = structlog.get_logger()


class Runtime:
    """Holds the wiring one request needs. Built once, by the composition root."""

    def __init__(
        self,
        unit_of_work: Callable[[], SqliteUnitOfWork],
        intake: Intake,
        line: Line,
        runner: Runner,
        clock: Clock,
        ids: IdGenerator,
        *,
        pack: str = "weight_dispute",
    ) -> None:
        self._uow = unit_of_work
        self._intake = intake
        self._line = line
        self.runner = runner
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

            # Saved after every transition, not once at the end. The database checks each
            # saved change is one legal step, so a case that moves several states in one
            # call - which every case does once the full line is wired - would otherwise be
            # refused as an illegal jump. This also covers the Phase 1 review's point: a
            # counter-only change is saved with its own transition, never discarded.
            advanced = self._line.advance(work, result.case, raised.id)

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

    def submit_inbound(self, event: InboundEvent) -> DispatchResult:
        """Any inbound event but a new dispute: a reply, a finding, a withdrawal, a bounce.

        People's decisions (approvals, taking or returning a case) are refused here until this
        endpoint authenticates a person: pre-dispatch rule P3 trusts the declared actor, so an
        open endpoint must not accept one (safety review of ADR 0033). The review console
        decides approvals in the store, and the runner applies them.
        """
        if event.type in HUMAN_EVENTS or event.type is InboundEventType.DISPUTE_RAISED:
            raise PermissionError(f"{event.type.value} is not accepted on this endpoint")
        return self.runner.submit(event)

    def read_case(self, case_id: str) -> tuple[Case, Sequence[Event]]:
        with self._uow() as work:
            case = work.cases.get(CaseId(case_id))
            events = work.events.list_for_case(CaseId(case_id))
        return case, events

    def _state_of(self, work: SqliteUnitOfWork, case_id: object) -> str | None:
        if case_id is None:
            return None
        return work.cases.get(CaseId(str(case_id))).state.value
