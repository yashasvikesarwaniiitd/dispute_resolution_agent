"""Intents, verdicts, approvals, actions and timers (kernel-interfaces.md 4.8 and 4.9).

The split between an intent and an action is the point of this file. An intent is what we
want to do, with its exact payload, created before any approval; an action is one execution
of an approved or allowed intent. Keeping them apart means the actions table only ever holds
things that were really started, so "did we send it?" is answerable from the record rather
than inferred.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
    TimerKind,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import (
    ActionId,
    ApprovalId,
    AssessmentId,
    CaseId,
    CorrelationId,
    IdempotencyKey,
    IntentId,
    PayloadHash,
    TimerId,
    VerdictId,
)
from dispute_agent.kernel.domain.models import Actor, ReviewReason
from dispute_agent.kernel.domain.patterns import CautionSignal
from dispute_agent.kernel.domain.reasoning import NumberUse
from dispute_agent.kernel.domain.values import Money, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


class MessagePayload(BaseModel):
    """`to` is always case.facts.seller_contact, never taken from a draft or a reply (R07)."""

    model_config = _FROZEN

    type: Literal["message"] = "message"
    kind: MessageKind
    to: str
    subject: str
    body: str
    numbers_used: tuple[NumberUse, ...] = ()
    in_reply_to: str | None = None


class IssuePayload(BaseModel):
    model_config = _FROZEN

    type: Literal["issue"] = "issue"
    kind: Literal["remeasurement"] = "remeasurement"
    summary: Annotated[str, Field(max_length=200)]
    description: Annotated[str, Field(max_length=4000)]
    labels: tuple[str, ...] = ()


class CreditPayload(BaseModel):
    """`fact_ids` names the assessment facts the amount is derived from (I9)."""

    model_config = _FROZEN

    type: Literal["credit"] = "credit"
    shipment_ref: str
    amount: Money
    reason_code: str
    fact_ids: tuple[str, ...] = ()

    @field_validator("amount")
    @classmethod
    def _positive(cls, value: Money) -> Money:
        if value.amount <= 0:
            raise DomainValidationError(f"a credit must be positive, not {value}")
        return value


type ActionPayload = Annotated[
    MessagePayload | IssuePayload | CreditPayload, Field(discriminator="type")
]

_PAYLOAD_FOR: dict[ActionType, str] = {
    ActionType.SEND_MESSAGE: "message",
    ActionType.UPSERT_ISSUE: "issue",
    ActionType.POST_CREDIT: "credit",
}


class ActionIntent(BaseModel):
    """What we want to do, with its exact payload, before anyone has approved it."""

    model_config = _FROZEN

    id: IntentId
    case_id: CaseId
    source_kind: IntentSource
    source_ref: str
    action_type: ActionType
    payload: ActionPayload
    payload_hash: PayloadHash
    assessment_id: AssessmentId | None = None
    status: IntentStatus
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "created_at")

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Self:
        expected = _PAYLOAD_FOR.get(self.action_type)
        if expected is not None and self.payload.type != expected:
            raise DomainValidationError(
                f"{self.action_type.value} takes a {expected} payload, not {self.payload.type}"
            )
        return self


class RuleResult(BaseModel):
    model_config = _FROZEN

    rule_id: str
    verdict: VerdictKind
    reason: Annotated[str, Field(max_length=200)]


class PolicyVerdict(BaseModel):
    """The decision, with every rule that ran.

    `verdict` is the maximum over the rule verdicts and the signal floors, which is what makes
    the lattice safe: adding a rule or a signal can only ever raise it (I7, I15).
    """

    model_config = _FROZEN

    id: VerdictId
    case_id: CaseId
    intent_id: IntentId | None = None
    stage: PolicyStage
    verdict: VerdictKind
    results: tuple[RuleResult, ...] = ()
    signals: tuple[CautionSignal, ...] = ()
    review_reason: ReviewReason | None = None
    rules_version: str
    evaluated_at: datetime

    @field_validator("evaluated_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "evaluated_at")

    @model_validator(mode="after")
    def _check_lattice(self) -> Self:
        floors = [result.verdict for result in self.results]
        floors += [signal.min_verdict for signal in self.signals]
        if floors and self.verdict != max(floors):
            raise DomainValidationError(
                f"verdict {self.verdict.name} is not the maximum of its inputs "
                f"({max(floors).name}); the lattice only ever takes the strictest"
            )
        if (self.review_reason is None) and self.verdict >= VerdictKind.ESCALATE:
            raise DomainValidationError(f"{self.verdict.name} needs a review reason")
        return self


class ApprovalRequest(BaseModel):
    model_config = _FROZEN

    approval_id: ApprovalId
    case_id: CaseId
    intent_id: IntentId
    subject: ApprovalSubject
    payload_hash: PayloadHash
    amount: Money | None = None
    message_kind: MessageKind | None = None
    requested_at: datetime
    expires_at: datetime

    @field_validator("requested_at", "expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "approval timestamp")

    @model_validator(mode="after")
    def _subject_fields(self) -> Self:
        if (self.amount is None) is (self.subject is ApprovalSubject.CREDIT):
            raise DomainValidationError("amount is set exactly for a CREDIT approval")
        if (self.message_kind is None) is (self.subject is ApprovalSubject.MESSAGE):
            raise DomainValidationError("message_kind is set exactly for a MESSAGE approval")
        if self.expires_at <= self.requested_at:
            raise DomainValidationError("an approval cannot expire before it was requested")
        return self


class Approval(BaseModel):
    """The stored approval record (I3, I13). The kernel's approvals table is the truth.

    `notified_at` is null until ApprovalService.request succeeded; the runner retries while it
    is. The decision itself never arrives through that port — it comes back as an
    APPROVAL_DECIDED inbound event (ADR 0013).
    """

    model_config = _FROZEN

    id: ApprovalId
    request: ApprovalRequest
    decision: ApprovalDecision
    decided_by: Actor | None = None
    decided_at: datetime | None = None
    note: Annotated[str, Field(max_length=500)] = ""
    used_by_action_id: ActionId | None = None
    notified_at: datetime | None = None

    @model_validator(mode="after")
    def _decided_together(self) -> Self:
        decided = self.decision in (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED)
        if decided and (self.decided_by is None or self.decided_at is None):
            raise DomainValidationError(
                f"{self.decision.value} needs both decided_by and decided_at"
            )
        if not decided and (self.decided_by is not None or self.decided_at is not None):
            raise DomainValidationError(f"{self.decision.value} cannot carry a decider")
        return self


class Action(BaseModel):
    """One execution of an approved or allowed intent."""

    model_config = _FROZEN

    id: ActionId
    case_id: CaseId
    intent_id: IntentId
    approval_id: ApprovalId | None = None
    action_type: ActionType
    action_seq: Annotated[int, Field(ge=1)]
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    status: ActionStatus
    attempts: Annotated[int, Field(ge=0)] = 0
    external_ref: str | None = None
    last_error_code: str | None = None
    planned_at: datetime
    sent_at: datetime | None = None
    verified_at: datetime | None = None
    verify_attempts: Annotated[int, Field(ge=0)] = 0
    verify_deadline_at: datetime | None = None
    verified_by: Actor | None = None
    #: Copied from the credit intent so the database can refuse a second live credit.
    #: schema.sql CHECKs both are present exactly for POST_CREDIT, and
    #: `ux_actions_one_credit` indexes them - left off the model, the index would be
    #: indexing (NULL, NULL) and enforcing nothing (I3). Found by the Phase 1 review.
    credit_shipment_ref: str | None = None
    credit_reason_code: str | None = None

    @field_validator("planned_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "planned_at")

    @model_validator(mode="after")
    def _credit_needs_approval(self) -> Self:
        """I3: no credit without a stored approval record, and nothing to index without."""
        is_credit = self.action_type is ActionType.POST_CREDIT
        if is_credit and self.approval_id is None:
            raise DomainValidationError("POST_CREDIT needs an approval (I3)")
        has_credit_keys = (
            self.credit_shipment_ref is not None and self.credit_reason_code is not None
        )
        if is_credit != has_credit_keys:
            raise DomainValidationError(
                "credit_shipment_ref and credit_reason_code are set exactly for POST_CREDIT; "
                "without them ux_actions_one_credit cannot refuse a duplicate (I3)"
            )
        return self


class Timer(BaseModel):
    """A deadline the case is waiting on.

    The four nullable end-states are different on purpose: `fired_at` means the scheduler
    raised it, `consumed_at` that the orchestrator acted on it, `voided_at` that it fired but
    a cancel effect made it stale before anyone acted, and `cancelled_at` that it never fired.
    Collapsing them would lose the difference between "we ignored it" and "it did not happen".
    """

    model_config = _FROZEN

    id: TimerId
    case_id: CaseId
    kind: TimerKind
    due_at: datetime
    anchor_at: datetime
    armed_in_state: CaseState
    armed_at_version: Annotated[int, Field(ge=1)]
    #: EVIDENCE_RETRY only (D41). The Timer model in kernel-interfaces.md section 4.9 does
    #: not list this field, but schema.sql requires it with a CHECK making it present exactly
    #: for that kind, and D41's "three retries per gather round" cannot be counted without
    #: it — per case, a second round of gathering would start already at the limit. The
    #: schema and the decision agree; the interface listing is the one that is short.
    gather_round: Annotated[int, Field(ge=1)] | None = None
    correlation_id: CorrelationId
    created_at: datetime
    fired_at: datetime | None = None
    consumed_at: datetime | None = None
    voided_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None

    @field_validator("due_at", "anchor_at", "created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "timer timestamp")

    @model_validator(mode="after")
    def _not_both_fired_and_cancelled(self) -> Self:
        if (self.gather_round is not None) != (self.kind is TimerKind.EVIDENCE_RETRY):
            raise DomainValidationError(
                "gather_round belongs to EVIDENCE_RETRY alone (D41, schema.sql)"
            )
        if self.fired_at is not None and self.cancelled_at is not None:
            raise DomainValidationError("a timer cannot both fire and be cancelled")
        if self.consumed_at is not None and self.fired_at is None:
            raise DomainValidationError("a timer cannot be consumed before it fires")
        if self.voided_at is not None and self.fired_at is None:
            raise DomainValidationError("a timer cannot be voided before it fires")
        return self
