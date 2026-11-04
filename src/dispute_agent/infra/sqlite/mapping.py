"""Domain models to rows and back (schema.sql conventions).

Two conversions carry weight. Timestamps are written as ISO 8601 UTC with exactly six
decimal places and a "Z", because the schema's GLOB checks that shape and because lexical
order is then time order — `ORDER BY due_at` on a text column is correct only if every value
is padded the same way.

Money is INTEGER paise and weight INTEGER grams (ADR 0011). Nothing here rounds: `Money`
already refuses a value with more than two decimal places, so `to_paise` is exact by the time
it gets here, and a float never reaches this module at all.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    CauseKind,
    InboundEventType,
    ReviewCode,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.events import (
    Event,
    EventPayload,
    EventRejectedPayload,
    StateChangedPayload,
    StepPayload,
)
from dispute_agent.kernel.domain.ids import (
    CaseId,
    CorrelationId,
    DedupeKey,
    EventId,
    IntentId,
)
from dispute_agent.kernel.domain.models import Actor, Case, Cause, ReviewReason

TIMESTAMP = "%Y-%m-%dT%H:%M:%S.%f"


def to_iso(value: datetime) -> str:
    """The exact shape schema.sql's GLOB accepts: microseconds, then a literal Z."""
    if value.tzinfo is None:
        raise DomainValidationError("naive datetime reached the database layer")
    return value.astimezone(UTC).strftime(TIMESTAMP) + "Z"


def from_iso(value: str) -> datetime:
    return datetime.strptime(value.removesuffix("Z"), TIMESTAMP).replace(tzinfo=UTC)


def optional_iso(value: datetime | None) -> str | None:
    return None if value is None else to_iso(value)


def optional_from_iso(value: str | None) -> datetime | None:
    return None if value is None else from_iso(value)


#: Payload classes that can be read back. A type not listed here round-trips as the bare
#: EventPayload, which is honest: Phase 1 writes the three it needs and the catalogue's rest
#: arrive with the steps that produce them (P1-05, P1-11).
PAYLOAD_TYPES: dict[str, type[EventPayload]] = {
    "STATE_CHANGED": StateChangedPayload,
    "EVENT_IGNORED": EventRejectedPayload,
    "EVENT_DEFERRED": EventRejectedPayload,
    "CASE_OPENED": StepPayload,
    # Written by transition effects and the dispatcher (kernel/orchestrator/effects.py).
    "TIMER_SCHEDULED": StepPayload,
    "TIMER_CANCELLED": StepPayload,
    "APPROVAL_SUPERSEDED": StepPayload,
    "ACTION_UNKNOWN": StepPayload,
    "CASE_OUTCOME_RECORDED": StepPayload,
    "REMINDER_SKIPPED": StepPayload,
    "PROPOSAL_RECORDED": StepPayload,
    "INTENT_CREATED": StepPayload,
    "APPROVAL_REQUESTED": StepPayload,
    "MODEL_ROUTE_FAILOVER": StepPayload,
}


def case_to_row(case: Case) -> dict[str, Any]:
    reason = case.review_reason
    return {
        "id": str(case.id),
        "pack": case.pack,
        "shipment_ref": case.facts.shipment_ref,
        "claim_ref": case.facts.claim_ref,
        "seller_ref": case.facts.seller_ref,
        "seller_contact": case.facts.seller_contact,
        "facts_json": case.facts.model_dump_json(),
        "state": case.state.value,
        "version": case.version,
        "correlation_id": str(case.correlation_id),
        "opened_at": to_iso(case.opened_at),
        "updated_at": to_iso(case.updated_at),
        "state_entered_at": to_iso(case.state_entered_at),
        "closed_at": optional_iso(case.closed_at),
        "review_code": None if reason is None else reason.code.value,
        "review_module": None if reason is None else reason.module,
        "review_detail": None if reason is None else (reason.detail or None),
        "pending_intent_id": None
        if case.pending_intent_id is None
        else str(case.pending_intent_id),
        "action_seq": case.action_seq,
        "reopen_count": case.reopen_count,
        "rejection_count": case.rejection_count,
        "gather_round": case.gather_round,
        "prior_case_id": None if case.prior_case_id is None else str(case.prior_case_id),
        "superseded_by_case_id": (
            None if case.superseded_by_case_id is None else str(case.superseded_by_case_id)
        ),
    }


def row_to_case(row: Any) -> Case:
    mapping = row._mapping if hasattr(row, "_mapping") else row
    reason = None
    if mapping["review_code"] is not None:
        reason = ReviewReason(
            code=ReviewCode(mapping["review_code"]),
            module=mapping["review_module"],
            detail=mapping["review_detail"] or "",
        )
    return Case(
        id=CaseId(mapping["id"]),
        pack=mapping["pack"],
        facts=CaseFacts.model_validate_json(mapping["facts_json"]),
        state=CaseState(mapping["state"]),
        version=mapping["version"],
        correlation_id=CorrelationId(mapping["correlation_id"]),
        opened_at=from_iso(mapping["opened_at"]),
        updated_at=from_iso(mapping["updated_at"]),
        state_entered_at=from_iso(mapping["state_entered_at"]),
        closed_at=optional_from_iso(mapping["closed_at"]),
        review_reason=reason,
        pending_intent_id=(
            None if mapping["pending_intent_id"] is None else IntentId(mapping["pending_intent_id"])
        ),
        action_seq=mapping["action_seq"],
        reopen_count=mapping["reopen_count"],
        rejection_count=mapping["rejection_count"],
        gather_round=mapping["gather_round"],
        prior_case_id=(
            None if mapping["prior_case_id"] is None else CaseId(mapping["prior_case_id"])
        ),
        superseded_by_case_id=(
            None
            if mapping["superseded_by_case_id"] is None
            else CaseId(mapping["superseded_by_case_id"])
        ),
    )


def event_category(event: Event) -> str:
    """INPUT for the eleven inbound types, INTERNAL for everything the kernel writes."""
    return "INPUT" if isinstance(event.type, InboundEventType) else "INTERNAL"


def event_to_row(event: Event) -> dict[str, Any]:
    payload = event.payload
    from_state = to_state = None
    if isinstance(payload, StateChangedPayload):
        from_state = payload.from_state.value
        to_state = payload.to_state.value
    return {
        "id": str(event.id),
        "case_id": str(event.case_id),
        "seq": event.seq,
        "type": event.type.value,
        "category": event_category(event),
        "occurred_at": to_iso(event.occurred_at),
        "recorded_at": to_iso(event.recorded_at),
        "actor_kind": event.actor.kind.value,
        "actor_id": event.actor.id,
        "cause_kind": event.cause.kind.value,
        "cause_ref": event.cause.ref,
        "correlation_id": str(event.correlation_id),
        "causation_id": None if event.causation_id is None else str(event.causation_id),
        "dedupe_key": None if event.dedupe_key is None else str(event.dedupe_key),
        "from_state": from_state,
        "to_state": to_state,
        "payload_json": payload.model_dump_json(),
        "schema_version": event.schema_version,
    }


def row_to_event(row: Any) -> Event:

    mapping = row._mapping if hasattr(row, "_mapping") else row
    payload_cls = PAYLOAD_TYPES.get(mapping["type"], EventPayload)
    return Event(
        id=EventId(mapping["id"]),
        case_id=CaseId(mapping["case_id"]),
        seq=mapping["seq"],
        type=_coerce_event_type(mapping["type"]),
        occurred_at=from_iso(mapping["occurred_at"]),
        recorded_at=from_iso(mapping["recorded_at"]),
        # Strict models: the column holds the enum's value, so it is converted here
        # rather than relying on coercion that strict mode deliberately does not do.
        actor=Actor(kind=ActorKind(mapping["actor_kind"]), id=mapping["actor_id"]),
        cause=Cause(kind=CauseKind(mapping["cause_kind"]), ref=mapping["cause_ref"]),
        correlation_id=CorrelationId(mapping["correlation_id"]),
        causation_id=None if mapping["causation_id"] is None else EventId(mapping["causation_id"]),
        dedupe_key=None if mapping["dedupe_key"] is None else DedupeKey(mapping["dedupe_key"]),
        payload=payload_cls.model_validate_json(mapping["payload_json"]),
        schema_version=mapping["schema_version"],
    )


def _coerce_event_type(value: str) -> Any:
    from dispute_agent.kernel.domain.enums import InternalEventType

    try:
        return InboundEventType(value)
    except ValueError:
        return InternalEventType(value)


def json_text(value: Any) -> str:
    """For the JSON columns that are not a model (results, signals, payloads)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
