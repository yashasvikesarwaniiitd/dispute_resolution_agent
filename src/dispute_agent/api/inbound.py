"""`POST /inbound` - everything that is not a new dispute: replies, findings, withdrawals, bounces.

The body is an `InboundEvent` in its stored JSON form, read with `parse_inbound`, so a payload
can only be read as the type its event names. A repeat of the same source event is answered
with the first one's result (the dedupe key).

People's decisions - approvals, taking or returning a case - are refused (403) until this
endpoint authenticates a person: dispatch's rule P3 trusts the declared actor, so an open
endpoint must never accept one. The review console decides approvals in the store.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.inbound import parse_inbound

if TYPE_CHECKING:
    from dispute_agent.api.runtime import Runtime


class InboundResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inbox_id: str
    case_id: str | None
    reaction: str
    state: str | None


def build_inbound_router(runtime: Runtime) -> APIRouter:
    router = APIRouter()

    @router.post("/inbound", response_model=InboundResult, status_code=200)
    def submit(body: dict[str, Any]) -> InboundResult:
        try:
            event = parse_inbound(json.dumps(body))
        except (ValidationError, DomainValidationError, KeyError, ValueError) as bad:
            raise HTTPException(status_code=422, detail=_fields(bad)) from None
        try:
            result = runtime.submit_inbound(event)
        except PermissionError as refused:
            raise HTTPException(status_code=403, detail=str(refused)) from None
        return InboundResult(
            inbox_id=str(result.inbox_id),
            case_id=None if result.case_id is None else str(result.case_id),
            reaction=result.reaction,
            state=None if result.state is None else result.state.value,
        )

    return router


def _fields(error: Exception) -> str:
    """Which fields were wrong, never their values: the body may hold a seller's words (I12)."""
    if isinstance(error, ValidationError):
        return "invalid fields: " + ", ".join(
            sorted({".".join(str(p) for p in e["loc"]) for e in error.errors()})
        )
    return f"invalid inbound event ({type(error).__name__})"


__all__ = ["InboundResult", "build_inbound_router"]
