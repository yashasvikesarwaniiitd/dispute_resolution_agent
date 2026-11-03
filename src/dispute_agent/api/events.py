"""`POST /events` — the way anything gets into the system (orchestrator.md 2, P1-11).

Submitting the same event twice is safe and returns the same answer. That is not a nicety: a
dispute form can be double-submitted, a mail poller can deliver twice, and an approval
console can be double-clicked. The dedupe key makes all three harmless, and this endpoint is
where that promise is kept.

A seller who is not on the test allowlist gets 422 and nothing is stored at all — not an
inbox row, not a case, not an event (I11, scenario S27).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from dispute_agent.kernel.domain.enums import ActorKind, InboundEventType
from dispute_agent.kernel.domain.errors import DuplicateEvent
from dispute_agent.kernel.domain.events import Event, EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.orchestrator.intake import RecipientNotAllowedError

if TYPE_CHECKING:
    from dispute_agent.api.runtime import Runtime

log = structlog.get_logger()


class RaiseDispute(BaseModel):
    """What the dispute form sends. Synthetic data only (I11)."""

    model_config = ConfigDict(extra="forbid")

    source: str = "dispute_api"
    source_event_id: str
    shipment_ref: str
    claim_ref: str
    seller_ref: str
    seller_contact: str
    raised_at: datetime


class SubmitResult(BaseModel):
    inbox_id: str
    duplicate: bool
    case_id: str | None = None
    state: str | None = None
    review_reason: str | None = None


def build_events_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    # The runtime is closed over rather than injected with Depends: `from __future__ import
    # annotations` plus a TYPE_CHECKING-only import leaves FastAPI unable to resolve the
    # annotation, and it silently treats the parameter as a query string instead.
    @router.post("/events", response_model=SubmitResult, status_code=200)
    def submit(body: RaiseDispute) -> SubmitResult:
        event = InboundEvent(
            type=InboundEventType.DISPUTE_RAISED,
            source=body.source,
            source_event_id=body.source_event_id,
            occurred_at=body.raised_at,
            actor=Actor(kind=ActorKind.EXTERNAL, id=body.source),
            payload=InboundPayload(),
        )
        try:
            return runtime.submit_dispute(event, body)
        except RecipientNotAllowedError as refused:
            # 422 and nothing stored. Refusing late would leave a case the system can never
            # safely act on (I11, S27).
            #
            # The message is fixed rather than str(refused), which embedded the submitted
            # address verbatim. Not an I5 violation - nothing merges it into instructions -
            # but echoing attacker-controlled text into a response body is a habit worth not
            # having. The address is in the exception for the logs, which are redacted.
            log.info("dispute_refused", reason="recipient_not_allowlisted")
            raise HTTPException(
                status_code=422,
                detail=(
                    "the seller contact is not on the test recipient allowlist; this "
                    "project writes only to the owner's own test addresses (I11)"
                ),
            ) from refused

    return router


def input_event(case_id: str, event: InboundEvent, inbox_id: str) -> Event:
    """The audit row for an inbound event (events-catalog.md section 2)."""
    from dispute_agent.kernel.domain.enums import CauseKind
    from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
    from dispute_agent.kernel.domain.models import Cause

    return Event(
        id=EventId(f"evt_{inbox_id.removeprefix('inb_')}"),
        case_id=CaseId(case_id),
        type=event.type,
        occurred_at=event.occurred_at,
        recorded_at=event.occurred_at,
        actor=event.actor,
        cause=Cause(kind=CauseKind.INPUT_EVENT, ref=inbox_id),
        correlation_id=CorrelationId("corr_" + inbox_id.removeprefix("inb_")),
        dedupe_key=event.dedupe_key,
        payload=EventPayload(),
    )


__all__ = ["DuplicateEvent", "RaiseDispute", "SubmitResult", "build_events_router", "input_event"]
