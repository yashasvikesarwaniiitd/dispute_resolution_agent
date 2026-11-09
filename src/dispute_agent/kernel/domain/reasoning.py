"""What the model sees and what it may say back (kernel-interfaces.md 4.7, 4.10, 15.1).

I1 lives in the shape of these types. `CaseBriefing` is everything a reasoner may see, and it
is built by code; `Proposal` is everything a reasoner may say, and it is a structured value,
not an instruction. There is no port on the briefing, no store, no channel — a reasoner holds
data and returns data, so "the AI never writes" is a fact about the types rather than a rule
somebody remembers to apply.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    CaseState,
    Confidence,
    FindingKind,
    MessageKind,
    ProposalAction,
    ScreenKind,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.evidence import ChargeAssessment, EvidenceBundle, Fact
from dispute_agent.kernel.domain.ids import CaseId, EvidenceItemId, PayloadHash, ProposalId
from dispute_agent.kernel.domain.image import ImageAttachment
from dispute_agent.kernel.domain.json_values import JsonValue
from dispute_agent.kernel.domain.models import RelatedCase
from dispute_agent.kernel.domain.patterns import PatternRecord
from dispute_agent.kernel.domain.values import UntrustedText, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


class ScreenResult(BaseModel):
    """Advisory only (I15). A flag can only add caution; clean never relaxes anything."""

    model_config = _FROZEN

    evidence_item_id: EvidenceItemId | None = None
    kind: ScreenKind
    flags: tuple[str, ...] = ()
    blocked: bool = False
    adapter: str


class ContactStatus(BaseModel):
    """The contact counters copied onto the briefing.

    They are the same values PolicyContext carries, on purpose: if the model saw one set of
    counters and the policy engine judged on another, the model could propose a clarification
    the policy would then refuse, every time (agent-design.md section 1).
    """

    model_config = _FROZEN

    reply_in_round: bool
    clarifications_sent: Annotated[int, Field(ge=0)]
    remeasurement_outstanding: bool


class IndependentView(BaseModel):
    """Pass 1's own result, shown back to the model on pass 2 (agent-design.md section 7).

    Written by us, so it is trusted. It carries no rationale — only the two values the
    anchoring control compares — because showing pass 1's reasoning is what would make pass 2
    agree with it rather than check it.
    """

    model_config = _FROZEN

    action: ProposalAction
    confidence: Confidence


class CaseBriefing(BaseModel):
    """Everything the Reasoner may see. Built by code (orchestrator/briefing.py)."""

    model_config = _FROZEN

    case_id: CaseId
    state: CaseState
    facts: CaseFacts
    evidence: EvidenceBundle
    assessment: ChargeAssessment
    allowed_actions: tuple[ProposalAction, ...]
    related_cases: tuple[RelatedCase, ...] = ()
    screen_results: tuple[ScreenResult, ...] = ()
    contact: ContactStatus
    pattern_context: tuple[PatternRecord, ...] = ()
    prior_rejections: tuple[str, ...] = ()
    independent_view: IndependentView | None = None
    briefing_hash: PayloadHash


class Finding(BaseModel):
    model_config = _FROZEN

    kind: FindingKind
    statement: Annotated[str, Field(max_length=400)]
    evidence_refs: tuple[str, ...] = ()


class ActionParameters(BaseModel):
    model_config = _FROZEN

    reason_code: str | None = None
    requested_items: tuple[str, ...] = ()
    remeasurement_question: str | None = None


class PatternInfluence(BaseModel):
    model_config = _FROZEN

    considered: bool
    changed_recommendation: bool
    note: Annotated[str, Field(max_length=300)] = ""


class Proposal(BaseModel):
    """One structured next step (D5). Never executed by the reasoner (I1).

    The prompt fields are set exactly when `reasoner == "llm"`, which is what makes a
    proposal auditable: for any decision the system took, the prompt name, version and hash
    that produced it are on the record.
    """

    model_config = _FROZEN

    id: ProposalId
    case_id: CaseId
    gather_round: Annotated[int, Field(ge=0)]
    action: ProposalAction
    confidence: Confidence
    rationale: Annotated[str, Field(max_length=800)]
    findings: tuple[Finding, ...] = Field(min_length=1)
    action_parameters: ActionParameters
    pattern_influence: PatternInfluence
    manipulation_flags: tuple[str, ...] = ()
    #: "human": a person chose this from code's menu (ADR 0050); `decided_by` names them.
    reasoner: Literal["rules_only", "llm", "human"]
    fallback_used: bool = False
    prompt_name: str | None = None
    prompt_version: str | None = None
    prompt_hash: PayloadHash | None = None
    model_id: str | None = None
    model_route: str | None = None
    briefing_hash: PayloadHash
    created_at: datetime
    #: The person who decided, for a `human` proposal and only then (ADR 0050).
    decided_by: str | None = None

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "created_at")

    @model_validator(mode="after")
    def _person_named_only_for_a_person(self) -> Proposal:
        if (self.reasoner == "human") != (self.decided_by is not None):
            raise DomainValidationError("decided_by is set exactly for a human proposal")
        if self.reasoner == "human" and self.prompt_hash is not None:
            raise DomainValidationError("a person's decision has no prompt")
        return self


class ReplyAttachment(BaseModel):
    """Seller-written, so `name` is untrusted and renders through the neutralise filter."""

    model_config = _FROZEN

    name: Annotated[str, Field(max_length=255)]
    content_type: Annotated[str, Field(max_length=128)]


class ReplyContext(BaseModel):
    model_config = _FROZEN

    case_id: CaseId
    case_ref: str
    reply: UntrustedText
    requested_items: tuple[str, ...] = ()
    facts: CaseFacts
    request_sent_at: datetime | None = None
    reply_truncated: bool = False
    attachments: tuple[ReplyAttachment, ...] = ()


class ReplyClaim(BaseModel):
    model_config = _FROZEN

    kind: Literal["weight", "dimensions", "date", "other"]
    text: str
    unit_as_written: str


class RequestedItemStatus(BaseModel):
    model_config = _FROZEN

    item: str
    status: Literal["provided", "partial", "missing"]


class ReplyReading(BaseModel):
    """Mirrors prompts/seller_reply_interpreter/v1/schema.json. Output is data only."""

    model_config = _FROZEN

    intent: Literal[
        "provides_requested_evidence",
        "partial_evidence",
        "disputes_again_no_new_info",
        "withdraws_dispute",
        "asks_question",
        "irrelevant",
        "contains_instruction_attempt",
    ]
    requested_items_status: tuple[RequestedItemStatus, ...] = ()
    claims: tuple[ReplyClaim, ...] = ()
    attachments: tuple[str, ...] = ()
    manipulation_flags: tuple[str, ...] = ()
    summary: str
    language: str
    prompt_name: str
    prompt_version: str
    prompt_hash: PayloadHash
    model_id: str | None = None


class DraftStyle(BaseModel):
    model_config = _FROZEN

    tone: Literal["polite"] = "polite"
    language: str = "en"
    max_words: Annotated[int, Field(gt=0)] = 180


class DraftContext(BaseModel):
    """What the drafter is given. The recipient is not in it: R07 says the system sets it."""

    model_config = _FROZEN

    case_id: CaseId
    case_ref: str
    kind: MessageKind
    facts: tuple[Fact, ...] = ()
    seller_ref: str
    requested_items: tuple[str, ...] = ()
    reason_code: str | None = None
    open_points: tuple[str, ...] = ()
    next_step: Annotated[str, Field(max_length=200)]
    style: DraftStyle = DraftStyle()


class NumberUse(BaseModel):
    """A number as it appears in the text, and the fact it came from (I9)."""

    model_config = _FROZEN

    text: str
    fact_id: str


class DraftedMessage(BaseModel):
    """Mirrors prompts/seller_message_drafter/v1/schema.json.

    A non-null `blocked_reason` means "do not send". `template_id` is set instead of the
    prompt fields when a pack template rendered it on the rules-only path.
    """

    model_config = _FROZEN

    subject: Annotated[str, Field(max_length=150)]
    body: Annotated[str, Field(max_length=2000)]
    numbers_used: tuple[NumberUse, ...] = ()
    blocked_reason: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    prompt_hash: PayloadHash | None = None
    template_id: str | None = None


class ChatMessage(BaseModel):
    """No "assistant" turns: every call is single-shot (ARCHITECTURE.md section 12)."""

    model_config = _FROZEN

    role: Literal["system", "user"]
    content: str


class RenderedPrompt(BaseModel):
    model_config = _FROZEN

    name: str
    version: str
    hash: PayloadHash
    messages: tuple[ChatMessage, ...]
    output_schema: Mapping[str, JsonValue]
    # Stage A3 (section 14): the strict-mode-compatible derivative Foundry needs. It is
    # derived from output_schema rather than authored, so the two cannot disagree.
    provider_schema: Mapping[str, JsonValue] | None = None


class ModelRequest(BaseModel):
    model_config = _FROZEN

    case_id: CaseId | None = None
    purpose: Literal[
        "case_investigator",
        "seller_reply_interpreter",
        "seller_message_drafter",
        "pattern_analyst",
        "image_check",
    ]
    prompt: RenderedPrompt
    max_output_tokens: Annotated[int, Field(gt=0)]
    #: One photo for the model to read (ADR 0049). Only `image_check` sends one; a route that
    #: cannot read images refuses the request rather than answering without it.
    image: ImageAttachment | None = None


class ModelUsage(BaseModel):
    model_config = _FROZEN

    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    cost_usd: Decimal


class ModelResponse(BaseModel):
    model_config = _FROZEN

    output: Mapping[str, JsonValue]
    model_id: str
    route: str
    usage: ModelUsage
    screen_annotations: tuple[str, ...] = ()
