"""`GET /cases/{id}` — the case and its diary (P1-11, observability.md).

The event list is the point of this endpoint. A state alone says where a case got to; the
events say how and why, each naming the transition that moved it and the cause that
triggered it. That is what makes a disputed decision reviewable months later.

Untrusted text is not returned here. A seller's own words live behind the evidence and
message endpoints, with their trust labels intact (I5); flattening them into a timeline is
how outside text ends up being read as fact.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from dispute_agent.kernel.domain.errors import NotFound
from dispute_agent.kernel.domain.events import StateChangedPayload

if TYPE_CHECKING:
    from dispute_agent.api.runtime import Runtime


class EventView(BaseModel):
    seq: int
    type: str
    occurred_at: str
    cause_kind: str
    cause_ref: str
    from_state: str | None = None
    to_state: str | None = None
    transition_id: str | None = None


class CaseView(BaseModel):
    id: str
    state: str
    version: int
    opened_at: str
    closed_at: str | None = None
    review_reason: str | None = None
    events: list[EventView]


def build_cases_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.get("/cases/{case_id}", response_model=CaseView)
    def get_case(case_id: str) -> CaseView:
        try:
            case, events = runtime.read_case(case_id)
        except NotFound as missing:
            raise HTTPException(status_code=404, detail=str(missing)) from missing

        views: list[EventView] = []
        for event in events:
            payload = event.payload
            change = payload if isinstance(payload, StateChangedPayload) else None
            views.append(
                EventView(
                    seq=event.seq,
                    type=event.type.value,
                    occurred_at=event.occurred_at.isoformat(),
                    cause_kind=event.cause.kind.value,
                    cause_ref=event.cause.ref,
                    from_state=None if change is None else change.from_state.value,
                    to_state=None if change is None else change.to_state.value,
                    transition_id=None if change is None else change.transition_id,
                )
            )

        return CaseView(
            id=str(case.id),
            state=case.state.value,
            version=case.version,
            opened_at=case.opened_at.isoformat(),
            closed_at=None if case.closed_at is None else case.closed_at.isoformat(),
            review_reason=None if case.review_reason is None else case.review_reason.render(),
            events=views,
        )

    return router
