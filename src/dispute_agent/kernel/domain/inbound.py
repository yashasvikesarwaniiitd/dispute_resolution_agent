"""Inbound payloads, one per event type (events-catalog.md section 2.1).

`InboundEvent.payload` is declared as the root `InboundPayload`; these are its concrete
types. The inbox stores the whole event as JSON, and `parse_inbound` is the one way back:
it picks the payload class by the event's own type, so a payload can never be read as a
different kind of event than the one that was accepted.

Free text from outside (a reply's subject and body, an ops note) is `UntrustedText` and
nothing else (I5). Nothing here interprets it; it is carried to where it is delimited.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from dispute_agent.kernel.domain.enums import InboundEventType, TimerKind
from dispute_agent.kernel.domain.events import InboundEvent, InboundPayload
from dispute_agent.kernel.domain.external import OpsFinding
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, PayloadHash, TimerId
from dispute_agent.kernel.domain.values import UntrustedText, require_aware


class SellerReplyReceived(InboundPayload):
    type: Literal["SELLER_REPLY_RECEIVED"] = "SELLER_REPLY_RECEIVED"
    external_message_id: str = Field(min_length=1)
    internet_message_id: str = ""
    conversation_id: str | None = None
    from_addr: str = Field(min_length=3)
    subject: UntrustedText
    body: UntrustedText
    attachments: tuple[str, ...] = ()
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "received_at")


class DisputeWithdrawn(InboundPayload):
    type: Literal["DISPUTE_WITHDRAWN"] = "DISPUTE_WITHDRAWN"
    shipment_ref: str
    claim_ref: str
    seller_ref: str
    withdrawn_at: datetime
    #: The portal's authenticated seller identity; must equal the case's seller (dispatch).
    authenticated_principal: str = Field(min_length=1)

    @field_validator("withdrawn_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "withdrawn_at")


class DeliveryFailureReceived(InboundPayload):
    type: Literal["DELIVERY_FAILURE_RECEIVED"] = "DELIVERY_FAILURE_RECEIVED"
    failed_external_message_id: str = Field(min_length=1)
    diagnostic_code: str = Field(max_length=20)


class OpsFindingReceived(InboundPayload):
    type: Literal["OPS_FINDING_RECEIVED"] = "OPS_FINDING_RECEIVED"
    finding: OpsFinding


class ApprovalDecided(InboundPayload):
    type: Literal["APPROVAL_DECIDED"] = "APPROVAL_DECIDED"
    approval_id: ApprovalId
    decision: Literal["APPROVED", "REJECTED"]
    #: What the approver was shown; dispatch ignores the event if it differs (I13).
    payload_hash_seen: PayloadHash
    note: str = Field(default="", max_length=500)


class TimerFired(InboundPayload):
    type: Literal["TIMER_FIRED"] = "TIMER_FIRED"
    timer_id: TimerId
    kind: TimerKind
    due_at: datetime

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "due_at")


class HumanTookCase(InboundPayload):
    type: Literal["HUMAN_TOOK_CASE"] = "HUMAN_TOOK_CASE"
    notify_seller: bool
    note: str = Field(default="", max_length=500)


class UnknownActionResolution(InboundPayload):
    action_id: ActionId
    resolution: Literal["confirmed_done", "confirmed_not_done"]
    evidence_note: str = Field(min_length=1, max_length=500)


class HumanReturnedCase(InboundPayload):
    type: Literal["HUMAN_RETURNED_CASE"] = "HUMAN_RETURNED_CASE"
    reason: str = Field(min_length=1, max_length=500)
    unknown_action_resolutions: tuple[UnknownActionResolution, ...] = ()


class HumanReopenedCase(InboundPayload):
    type: Literal["HUMAN_REOPENED_CASE"] = "HUMAN_REOPENED_CASE"
    reason: str = Field(min_length=1, max_length=500)


class HumanConfirmedWithdrawal(InboundPayload):
    type: Literal["HUMAN_CONFIRMED_WITHDRAWAL"] = "HUMAN_CONFIRMED_WITHDRAWAL"
    note: str = Field(default="", max_length=500)


PAYLOADS: dict[InboundEventType, type[InboundPayload]] = {
    InboundEventType.SELLER_REPLY_RECEIVED: SellerReplyReceived,
    InboundEventType.DISPUTE_WITHDRAWN: DisputeWithdrawn,
    InboundEventType.DELIVERY_FAILURE_RECEIVED: DeliveryFailureReceived,
    InboundEventType.OPS_FINDING_RECEIVED: OpsFindingReceived,
    InboundEventType.APPROVAL_DECIDED: ApprovalDecided,
    InboundEventType.TIMER_FIRED: TimerFired,
    InboundEventType.HUMAN_TOOK_CASE: HumanTookCase,
    InboundEventType.HUMAN_RETURNED_CASE: HumanReturnedCase,
    InboundEventType.HUMAN_REOPENED_CASE: HumanReopenedCase,
    InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL: HumanConfirmedWithdrawal,
    # DISPUTE_RAISED's facts travel to intake separately (api/events.py); its stored
    # payload is the bare root.
    InboundEventType.DISPUTE_RAISED: InboundPayload,
}

#: Events that only a person may send (pre-dispatch rule P3, events-catalog.md 2).
HUMAN_EVENTS = frozenset(
    {
        InboundEventType.APPROVAL_DECIDED,
        InboundEventType.HUMAN_TOOK_CASE,
        InboundEventType.HUMAN_RETURNED_CASE,
        InboundEventType.HUMAN_REOPENED_CASE,
        InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL,
    }
)


def parse_inbound(event_json: str) -> InboundEvent:
    """The stored event, with its payload read as the class its own type names."""
    raw = json.loads(event_json)
    payload_raw = raw.pop("payload", {}) or {}
    raw.pop("dedupe_key", None)  # derived, never stored as input
    shell = InboundEvent.model_validate_json(json.dumps({**raw, "payload": {}}))
    payload_cls = PAYLOADS[shell.type]
    payload = payload_cls.model_validate_json(json.dumps(payload_raw))
    return shell.model_copy(update={"payload": payload})


def reaction_key(event: InboundEvent) -> str:
    """The acceptance-matrix key: TIMER_FIRED is split by timer kind."""
    if isinstance(event.payload, TimerFired):
        return f"TIMER_FIRED.{event.payload.kind.value}"
    return event.type.value


__all__ = [
    "HUMAN_EVENTS",
    "PAYLOADS",
    "ApprovalDecided",
    "DeliveryFailureReceived",
    "DisputeWithdrawn",
    "HumanConfirmedWithdrawal",
    "HumanReopenedCase",
    "HumanReturnedCase",
    "HumanTookCase",
    "OpsFindingReceived",
    "SellerReplyReceived",
    "TimerFired",
    "UnknownActionResolution",
    "parse_inbound",
    "reaction_key",
]
