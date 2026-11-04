"""The audit trail (kernel-interfaces.md section 4.4, events-catalog.md).

The events table is the case's diary and it is append-only (I8): never updated, never
deleted. `EventLog` has no update method, and these models carry no way to express an edit.

On payloads. events-catalog.md defines one payload schema per event type — eleven inbound
types and the internal ones — as tables rather than as Python. Transcribing all of them is
Phase 1 work, where intake and the step tables actually read them (cards P1-05 and P1-11).
Until then the two union roots are declared as frozen base models, so a payload is a typed
object rather than a dict (the P0-05 card's rule: ports take and return frozen models), and
the concrete subclasses attach to these roots without changing anything here.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    computed_field,
    field_validator,
    model_validator,
)

from dispute_agent.kernel.domain.enums import CaseState, EventType, InboundEventType, Trigger
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, DedupeKey, EventId
from dispute_agent.kernel.domain.models import Actor, Cause, ReviewReason
from dispute_agent.kernel.domain.values import require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)

#: The sources an inbound event may claim (events-catalog.md section 2).
INBOUND_SOURCES = frozenset(
    {
        "dispute_api",
        "outlook",
        "seller_portal",
        "jira",
        "approvals",
        "scheduler",
        "ops_console",
        "simulator",
    }
)


class InboundPayload(BaseModel):
    """Root of the inbound payload union. Concrete types land here in Phase 1."""

    model_config = _FROZEN


class EventPayload(BaseModel):
    """Root of the audit payload union. Concrete types land here in Phase 1.

    Payloads never contain secrets (I12), and untrusted text appears only as an
    `UntrustedText`, never merged into another string (I5).
    """

    model_config = _FROZEN


class StateChangedPayload(EventPayload):
    """The first concrete payload, because the state machine cannot work without it.

    `schema.sql` denormalises `from_state` and `to_state` onto the events row for timeline
    queries, and its CHECK requires `to_state` exactly when the type is STATE_CHANGED — so
    this is not an optional convenience, it is what makes a state change storable.

    `transition_id` is the T-number from transitions.yaml. Recording it means a row in the
    diary can be traced back to the rule that permitted it, which is the difference between
    an audit trail and a log.
    """

    from_state: CaseState
    to_state: CaseState
    transition_id: str
    trigger: Trigger
    review_reason: ReviewReason | None = None


class EventRejectedPayload(EventPayload):
    """An inbound event the acceptance matrix refused.

    Recorded rather than dropped (P1-04): "we ignored this" is a fact about the case, and a
    seller who replied to a closed case deserves better than silence in the record.
    """

    event_type: str
    state: CaseState
    reaction: str


class StepPayload(EventPayload):
    """A step ran. `detail` is staff-facing: no untrusted text, no secrets (I5, I12)."""

    step: str
    detail: str = ""


class InboundEvent(BaseModel):
    """Anything that arrives from outside the orchestrator.

    `dedupe_key` is derived, not stored as a field, so two submissions of the same source
    event cannot disagree about their own identity.
    """

    model_config = _FROZEN

    type: InboundEventType
    source: str
    source_event_id: Annotated[str, Field(min_length=1, max_length=256)]
    occurred_at: datetime
    actor: Actor
    case_id: CaseId | None = None
    correlation_id: CorrelationId | None = None
    #: SerializeAsAny: serialised as the concrete payload, not the root. Without it the
    #: inbox stored `{}` for every payload and a reply lost its body on the way in.
    payload: SerializeAsAny[InboundPayload]

    @field_validator("occurred_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "occurred_at")

    @field_validator("source")
    @classmethod
    def _known_source(cls, value: str) -> str:
        if value not in INBOUND_SOURCES:
            raise DomainValidationError(
                f"unknown inbound source {value!r}; events-catalog.md section 2 lists them"
            )
        return value

    @computed_field  # type: ignore[prop-decorator]
    @property
    def dedupe_key(self) -> DedupeKey:
        return DedupeKey(f"{self.source}:{self.source_event_id}")


class Event(BaseModel):
    """One row of the append-only audit trail (I8).

    `seq` is 0 when the kernel builds one and is assigned by `EventLog.append`, which returns
    the event carrying its real value. `causation_id` is None only for input events: every
    internal event names the input whose handling produced it, which is what makes the diary
    readable backwards from an outcome to the thing that caused it.
    """

    model_config = _FROZEN

    id: EventId
    case_id: CaseId
    seq: Annotated[int, Field(ge=0)] = 0
    type: EventType
    occurred_at: datetime
    recorded_at: datetime
    actor: Actor
    cause: Cause
    correlation_id: CorrelationId
    causation_id: EventId | None = None
    dedupe_key: DedupeKey | None = None
    payload: EventPayload
    schema_version: Annotated[int, Field(ge=1)] = 1

    @field_validator("occurred_at", "recorded_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "event timestamp")

    @model_validator(mode="after")
    def _dedupe_key_for_inputs_only(self) -> Self:
        is_input = isinstance(self.type, InboundEventType)
        if not is_input and self.dedupe_key is not None:
            raise DomainValidationError(
                "internal events carry no dedupe key; (case_id, seq) makes them unique"
            )
        if not is_input and self.causation_id is None:
            raise DomainValidationError(
                f"internal event {self.type.value} must name the input it was produced under"
            )
        return self
