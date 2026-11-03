"""What the write ports send and read back (kernel-interfaces.md section 6.4).

Every write port has the same three-part shape, and these are its nouns: a request, a receipt
and a snapshot. The snapshot is the one that matters for I2 — the executor re-reads what
actually exists at the far end and compares it with what it meant to do, rather than
believing the receipt. `duplicate` on a receipt says the adapter recognised the idempotency
key and did not write again.

Inbound text arrives as UntrustedText, from the subject line onwards. A subject is written by
whoever sent the mail (I5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from dispute_agent.kernel.domain.enums import MessageKind
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey, PayloadHash
from dispute_agent.kernel.domain.json_values import JsonScalar
from dispute_agent.kernel.domain.values import Money, UntrustedText, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


class OutboundMessage(BaseModel):
    model_config = _FROZEN

    to: str
    subject: str
    body: str
    in_reply_to: str | None = None
    case_id: CaseId
    kind: MessageKind


class SendReceipt(BaseModel):
    model_config = _FROZEN

    external_message_id: str
    conversation_id: str | None = None
    sent_at: datetime
    duplicate: bool = False

    @field_validator("sent_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "sent_at")


class MessageSnapshot(BaseModel):
    """Read-back. `body` is the plain text exactly as sent; the executor hashes it (I13)."""

    model_config = _FROZEN

    external_message_id: str
    to: tuple[str, ...]
    subject: str
    body: str
    idempotency_key: IdempotencyKey | None = None
    in_sent_items: bool = False
    conversation_id: str | None = None


class AttachmentRef(BaseModel):
    model_config = _FROZEN

    name: str
    content_type: str
    size_bytes: int
    content_hash: PayloadHash


class InboundMessage(BaseModel):
    """`internet_message_id` is sender-controlled; routing uses the store's own id."""

    model_config = _FROZEN

    external_message_id: str
    internet_message_id: str
    conversation_id: str | None = None
    from_addr: str
    to_addr: str
    subject: UntrustedText
    body: UntrustedText
    received_at: datetime
    attachments: tuple[AttachmentRef, ...] = ()
    is_delivery_failure: bool = False
    failed_message_id: str | None = None

    @field_validator("received_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "received_at")

    @model_validator(mode="after")
    def _failure_names_its_message(self) -> Self:
        if self.is_delivery_failure and not self.failed_message_id:
            raise DomainValidationError(
                "a delivery failure must name the message that failed, or it cannot be routed"
            )
        return self


class IssueSpec(BaseModel):
    model_config = _FROZEN

    project_key: str
    summary: str
    description: str
    labels: tuple[str, ...] = ()
    case_id: CaseId


class IssueRef(BaseModel):
    model_config = _FROZEN

    key: str
    id: str
    duplicate: bool = False


class IssueSnapshot(BaseModel):
    """`description_text` is plain text extracted from the stored ADF and normalised."""

    model_config = _FROZEN

    key: str
    project_key: str
    labels: tuple[str, ...] = ()
    summary: str
    description_text: str
    status: str
    resolution: str | None = None
    idempotency_key: IdempotencyKey | None = None


class OpsFinding(BaseModel):
    model_config = _FROZEN

    issue_key: str
    finding_id: str
    kind: Literal["remeasured", "inspected", "no_fault_found"]
    measured: dict[str, JsonScalar] = {}
    note: UntrustedText | None = None
    at: datetime

    @field_validator("at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "finding time")


class CreditRequest(BaseModel):
    model_config = _FROZEN

    shipment_ref: str
    amount: Money
    reason_code: str
    case_id: CaseId


class TransactionRef(BaseModel):
    model_config = _FROZEN

    transaction_id: str
    duplicate: bool = False


class TransactionSnapshot(BaseModel):
    model_config = _FROZEN

    transaction_id: str
    shipment_ref: str
    amount: Money
    reason_code: str
    type: Literal["CREDIT", "DEBIT"]
    status: Literal["POSTED", "PENDING", "REVERSED"]
    idempotency_key: IdempotencyKey
    posted_at: datetime | None = None


class LedgerEntry(BaseModel):
    model_config = _FROZEN

    transaction_id: str
    shipment_ref: str
    type: Literal["CREDIT", "DEBIT"]
    amount: Money
    reason_code: str
    posted_at: datetime

    @field_validator("posted_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "posted_at")
