# Kernel interfaces (Stage A1)

Status: draft for owner approval. Authority: this file beats the sketches in `docs/ARCHITECTURE.md` section 4 (CLAUDE.md section 18). Every difference from that sketch is listed in section 12.

Plain-language summary: the kernel is the part of the system that decides. It owns the data shapes (cases, events, proposals, actions), the rules for moving a case, and a set of sockets (ports) that the outside world plugs into. This document gives the exact Python shape of every one of those, so that Phase 0 and Phase 1 can be written without making design decisions.

Conventions used everywhere below:

- Python 3.12, pydantic v2. Every domain model is `model_config = ConfigDict(frozen=True, extra="forbid", strict=True)` unless stated. "Frozen" means a value cannot be changed after creation; to change a case you create a new `Case` with `model_copy(update=...)`.
- All datetimes are timezone-aware UTC (`datetime` with `tzinfo=UTC`). A naive datetime raises `DomainValidationError`. Time comes only from the `Clock` port.
- Money is `Decimal` INR with exactly two decimal places; weight is `Decimal` kilograms with exactly three. Floats are rejected by validators (I9, CLAUDE.md section 7).
- "Pre" lists preconditions the caller must ensure; "Post" lists what is guaranteed on normal return; "Raises" lists every typed error. A port method raises only the errors listed (adapters map every library error to one of them).
- File paths are under `src/dispute_agent/kernel/`.

## 1. Identifiers (`domain/ids.py`)

```python
from typing import NewType

CaseId = NewType("CaseId", str)            # "case_" + 26-char ULID
EventId = NewType("EventId", str)          # "evt_"
InboxId = NewType("InboxId", str)          # "inb_"
EvidenceItemId = NewType("EvidenceItemId", str)  # "evi_"
AssessmentId = NewType("AssessmentId", str)      # "asm_"
ProposalId = NewType("ProposalId", str)    # "prp_"
IntentId = NewType("IntentId", str)        # "int_"
VerdictId = NewType("VerdictId", str)      # "vrd_"
ApprovalId = NewType("ApprovalId", str)    # "apr_"
ActionId = NewType("ActionId", str)        # "act_"
MessageId = NewType("MessageId", str)      # "msg_"
TimerId = NewType("TimerId", str)          # "tmr_"
OutcomeId = NewType("OutcomeId", str)      # "out_"
PatternId = NewType("PatternId", str)      # "pat_"
CorrelationId = NewType("CorrelationId", str)  # "corr_"
DedupeKey = NewType("DedupeKey", str)      # "<source>:<source_event_id>", 1..300 chars
IdempotencyKey = NewType("IdempotencyKey", str)  # "wd1:" + 64 hex chars (executor.md section 2)
PayloadHash = NewType("PayloadHash", str)  # "sha256:" + 64 hex chars (section 2.3)

ID_PREFIXES: Final[frozenset[str]] = frozenset({"case", "evt", "inb", "evi", "asm", "prp", "int",
    "vrd", "apr", "act", "msg", "tmr", "out", "pat", "corr"})
```

`IdGenerator.new_id(prefix)` returns `f"{prefix}_{ulid}"` where `ulid` is 26 Crockford base32 characters. ULIDs sort by creation time, which keeps ids roughly ordered in SQLite indexes. The fake generator returns `f"{prefix}_{n:026d}"` with a per-prefix counter, so tests are deterministic.

## 2. Value objects (`domain/values.py`)

### 2.1 Money

```python
class Money(BaseModel):
    """An exact INR amount. Never negative; direction (debit or credit) is expressed by the owner."""
    amount: Decimal          # finite, >= 0, exponent exactly -2 (Decimal("12.50"), not "12.5")
    currency: Literal["INR"] = "INR"

    @classmethod
    def of(cls, value: str | int | Decimal) -> "Money":
        """Parse. str and Decimal must already have at most 2 places; it is quantized to 2.
        Raises DomainValidationError for floats, NaN, infinity, negatives or more than 2 places."""
    @classmethod
    def from_paise(cls, paise: int) -> "Money": ...
    def to_paise(self) -> int: ...
    def __add__(self, other: "Money") -> "Money": ...
    def __sub__(self, other: "Money") -> "Money":
        """Raises DomainValidationError if the result would be negative."""
    def __lt__(self, other: "Money") -> bool: ...   # plus le, gt, ge; == compares amount exactly
```

Rounding: none, ever, inside the kernel. A value with more than two places is a validation error, so a rounding decision can only be made by the domain pack, explicitly (A2 `money-rules.md` defines where).

### 2.2 Weight

```python
class Weight(BaseModel):
    """Kilograms with exactly 3 decimal places (gram precision). >= 0."""
    kg: Decimal
    @classmethod
    def of(cls, value: str | int | Decimal) -> "Weight": ...   # same rules as Money.of, 3 places
    @classmethod
    def from_grams(cls, grams: int) -> "Weight": ...
    def to_grams(self) -> int: ...
```

### 2.3 Canonical JSON and payload hashes (`domain/hashing.py`)

```python
def canonical_json(value: JsonValue | BaseModel) -> str:
    """Deterministic JSON: keys sorted, separators (",", ":"), ensure_ascii=False, UTF-8.
    Decimal -> its string form ("12.50"); datetime -> ISO 8601 UTC with "Z" and microseconds;
    Enum -> its value; BaseModel -> model_dump(mode="python") first. Floats raise DomainValidationError."""

def payload_hash(value: JsonValue | BaseModel) -> PayloadHash:
    """'sha256:' + hex sha256 of canonical_json(value).encode('utf-8')."""
```

Every comparison of "the text approved" against "the text sent" (I13), and every idempotency key (executor.md section 2), uses `payload_hash`. Two payloads are the same if and only if their hashes are equal.

### 2.4 Untrusted text

```python
class UntrustedText(BaseModel):
    """Text from outside the system (I5). It is data: never interpolated into instructions,
    never parsed for commands. The only way to put it in a prompt is inside a delimited block
    rendered by the prompt registry."""
    text: str                        # max 20_000 chars; longer input is truncated by the adapter with truncated=True
    origin: Literal["seller_email", "seller_portal", "jira_comment", "shipment_note", "dispute_form"]
    source_ref: str                  # e.g. the inbound message id
    truncated: bool = False
    content_hash: PayloadHash        # payload_hash(text); computed by the constructor helper
```

## 3. Enumerations (`domain/enums.py`)

All are `StrEnum` except `VerdictKind` (`IntEnum`). Shorthand: `class X(StrEnum): A; B` means members whose value is exactly the member name (`A = "A"`); write explicit values in the implementation, never `auto()` (it lower-cases). Where a value differs from the name it is shown. The values are the persisted strings; schema.sql CHECK constraints list the same values.

```python
class CaseState(StrEnum):
    RECEIVED; GATHERING_EVIDENCE; CALCULATING; INVESTIGATING; NEEDS_HUMAN_REVIEW
    AWAITING_SELLER; AWAITING_OPS; AWAITING_APPROVAL; EXECUTING; VERIFYING
    RESOLVED_CREDITED; RESOLVED_UPHELD; WITHDRAWN; CLOSED_NO_RESPONSE; REJECTED_INELIGIBLE; HANDED_TO_HUMAN

class StateCategory(StrEnum): AUTO; AUTO_MAY_REST; WAITING; HUMAN; TERMINAL
class ReopenRule(StrEnum): HUMAN; AGENT_WITHIN_WINDOW; NEVER   # terminal states only

class InboundEventType(StrEnum):
    DISPUTE_RAISED; SELLER_REPLY_RECEIVED; DISPUTE_WITHDRAWN; DELIVERY_FAILURE_RECEIVED
    OPS_FINDING_RECEIVED; APPROVAL_DECIDED; TIMER_FIRED
    HUMAN_TOOK_CASE; HUMAN_RETURNED_CASE; HUMAN_REOPENED_CASE; HUMAN_CONFIRMED_WITHDRAWAL

class InternalEventType(StrEnum):   # catalog and payloads: events-catalog.md
    CASE_OPENED; STATE_CHANGED; ELIGIBILITY_EVALUATED; EVIDENCE_RECORDED; ASSESSMENT_RECORDED
    INPUT_SCREENED; PATTERN_CONTEXT_LOADED; PROPOSAL_RECORDED; REASONER_FALLBACK; INTENT_CREATED
    POLICY_EVALUATED; APPROVAL_REQUESTED; APPROVAL_SUPERSEDED; ACTION_PLANNED; ACTION_SENT
    ACTION_FAILED; ACTION_UNKNOWN; VERIFICATION_PENDING; ACTION_VERIFIED; UNKNOWN_ACTION_RESOLVED
    TIMER_SCHEDULED; TIMER_CANCELLED; EVENT_DEFERRED; EVENT_IGNORED; REMINDER_SKIPPED
    NOTICE_NOT_SENT; CASE_OUTCOME_RECORDED; MODEL_ROUTE_FAILOVER

EventType = InboundEventType | InternalEventType

class TimerKind(StrEnum): SELLER_REMINDER; SELLER_DECISION; OPS_TIMEOUT; APPROVAL_EXPIRY; VERIFY_RETRY

class Trigger(StrEnum):   # exactly the `triggers` keys of transitions.yaml
    CASE_OPENED; ELIGIBILITY_PASSED; ELIGIBILITY_FAILED; EVIDENCE_RECORDED; ASSESSMENT_READY
    ASSESSMENT_INELIGIBLE; VERDICT_ALLOW; VERDICT_REQUIRE_APPROVAL; PROPOSAL_WAIT; REMINDER_ALLOW
    REMINDER_REQUIRE_APPROVAL; FOLLOW_UP_ALLOW; FOLLOW_UP_REQUIRE_APPROVAL; FOLLOW_UP_BLOCKED
    ACTION_SENT; ACTION_VERIFIED; APPROVAL_GRANTED; APPROVAL_REJECTED; SELLER_REPLY_RECEIVED
    OPS_FINDING_RECEIVED; DISPUTE_WITHDRAWN; DISPUTE_RAISED_AGAIN; SELLER_DECISION_DUE
    HUMAN_TOOK_CASE; HUMAN_RETURNED_CASE; HUMAN_REOPENED_CASE; HUMAN_CONFIRMED_WITHDRAWAL
    LATE_INBOUND; ESCALATE

class ProposalAction(StrEnum):   # mirrors prompts/case_investigator/v1/schema.json
    PROPOSE_CREDIT = "propose_credit"; UPHOLD_CHARGE = "uphold_charge"
    REQUEST_SELLER_EVIDENCE = "request_seller_evidence"; REQUEST_REMEASUREMENT = "request_remeasurement"
    ESCALATE_TO_HUMAN = "escalate_to_human"; WAIT = "wait"

class Confidence(StrEnum): LOW = "low"; MEDIUM = "medium"; HIGH = "high"
class FindingKind(StrEnum):
    VERIFIED_FACT = "verified_fact"; PLAUSIBLE_EXPLANATION = "plausible_explanation"
    MISSING_EVIDENCE = "missing_evidence"; CONFLICTING_EVIDENCE = "conflicting_evidence"

class ActionType(StrEnum): SEND_MESSAGE; UPSERT_ISSUE; POST_CREDIT
class MessageKind(StrEnum):
    EVIDENCE_REQUEST = "evidence_request"; REMINDER = "reminder"
    HANDED_TO_HUMAN_NOTICE = "handed_to_human_notice"; UPHELD_EXPLANATION = "upheld_explanation"
    CREDIT_CONFIRMATION = "credit_confirmation"
class VerifiedKind(StrEnum):   # the `verified_kind` carried by Trigger.ACTION_VERIFIED
    EVIDENCE_REQUEST; REMINDER; REMEASUREMENT_ISSUE; UPHELD_EXPLANATION; CREDIT
    CREDIT_CONFIRMATION; HANDED_TO_HUMAN_NOTICE

class IntentSource(StrEnum): PROPOSAL; TIMER; FOLLOW_UP
class IntentStatus(StrEnum): PENDING_APPROVAL; APPROVED; PLANNED; SUPERSEDED; REJECTED; EXPIRED
    # ALLOW: created then PLANNED in the same transaction. REQUIRE_APPROVAL: PENDING_APPROVAL ->
    # APPROVED (decision) -> PLANNED (executor.plan), or -> REJECTED / SUPERSEDED / EXPIRED.
class ActionStatus(StrEnum): PLANNED; SENT; VERIFIED; FAILED; UNKNOWN
class ApprovalSubject(StrEnum): CREDIT; MESSAGE
class ApprovalDecision(StrEnum): PENDING; APPROVED; REJECTED; EXPIRED; SUPERSEDED
class EvidenceStatus(StrEnum): FOUND; MISSING; FAILED
class Provenance(StrEnum): SYSTEM; SELLER; OPS; AI_DERIVED
class VerdictKind(IntEnum):     # ordered by caution; combining verdicts takes the max (section 7)
    ALLOW = 0; REQUIRE_APPROVAL = 1; ESCALATE = 2; DENY = 3
class PolicyStage(StrEnum): PLANNING; EXECUTION
class InboxStatus(StrEnum): PENDING; PROCESSED; DEFERRED; UNROUTABLE; FAILED
class Reaction(StrEnum): TRANSITION; STEP; ABSORB; DEFER; IGNORE
class CauseKind(StrEnum): INPUT_EVENT; STEP; TIMER; HUMAN; RECOVERY
class ActorKind(StrEnum): SYSTEM; HUMAN; EXTERNAL
class ScreenKind(StrEnum): SELLER_REPLY; OPS_NOTE; DISPUTE_STATEMENT; SHIPMENT_NOTE

class ReviewCode(StrEnum):
    MODULE_UNAVAILABLE = "module_unavailable"       # rendered "module_unavailable:<module>" (I6)
    KILL_SWITCH_ACTIVE = "kill_switch_active"
    ELIGIBILITY_UNKNOWN = "eligibility_unknown"
    PROPOSAL_ESCALATED = "proposal_escalated"
    POLICY_DENIED = "policy_denied"
    POLICY_ESCALATED = "policy_escalated"
    WAIT_WITHOUT_EXPECTATION = "wait_without_expectation"
    SELLER_WITHDRAWAL_CLAIMED = "seller_withdrawal_claimed"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    LLM_BUDGET_EXCEEDED = "llm_budget_exceeded"
    CREDIT_CAP_REACHED = "credit_cap_reached"
    AGENT_DRIFT = "agent_drift"
    INVALID_MODEL_OUTPUT = "invalid_model_output"
    ACTION_FAILED = "action_failed"
    ACTION_UNKNOWN = "action_unknown"
    VERIFICATION_FAILED = "verification_failed"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_REJECTED_LIMIT = "approval_rejected_limit"
    FOLLOW_UP_REJECTED = "follow_up_rejected"
    FOLLOW_UP_DENIED = "follow_up_denied"
    OPS_TIMEOUT = "ops_timeout"
    DELIVERY_FAILED = "delivery_failed"
    LATE_INBOUND = "late_inbound"
    STEP_LIMIT = "step_limit"
    INTERNAL_ERROR = "internal_error"
```

## 4. Domain models (`domain/models.py`, `domain/events.py`)

### 4.1 Supporting models

```python
class Actor(BaseModel):
    kind: ActorKind
    id: str                     # "system" | "human:<user id>" | "<source name>" for EXTERNAL; 1..120 chars

class Cause(BaseModel):
    """Why something happened. Every Event and every state change has one (I8)."""
    kind: CauseKind
    ref: str                    # EventId for INPUT_EVENT/TIMER/HUMAN; step name for STEP; recovery run id for RECOVERY

class ReviewReason(BaseModel):
    code: ReviewCode
    module: str | None = None   # required iff code == MODULE_UNAVAILABLE; a registry module name
    detail: str = ""            # <= 300 chars, no untrusted text, no secrets
    def render(self) -> str:    # "module_unavailable:email" or the code value
        ...
```

### 4.2 CaseFacts

```python
class CaseFacts(BaseModel):
    """The facts a dispute arrives with. Frozen at intake; later knowledge is evidence, not facts."""
    shipment_ref: str            # 1..64, [A-Za-z0-9_-]
    claim_ref: str               # the disputed charge line, 1..64
    seller_ref: str              # 1..64
    seller_contact: str          # the seller's email address (synthetic, I11); validated as an address
    raised_at: datetime
    disputed_amount: Money | None
    statement: UntrustedText | None   # the seller's own words, if any
    pack_facts: Mapping[str, JsonScalar]   # validated by DomainPack.parse_facts; e.g. sku, hub, declared dims
```

`JsonScalar = str | int | bool | None | Decimal` (no floats). `pack_facts` stays a flat mapping so the kernel remains domain-neutral; the pack owns the meaning.

### 4.3 Case

```python
class Case(BaseModel):
    id: CaseId
    pack: str                         # domain pack name, e.g. "weight_dispute"
    facts: CaseFacts
    state: CaseState
    version: int                      # >= 1; bumped by CaseRepository.save only
    correlation_id: CorrelationId     # root correlation for the case (the DISPUTE_RAISED's)
    opened_at: datetime
    updated_at: datetime
    state_entered_at: datetime
    closed_at: datetime | None        # set iff state is terminal
    review_reason: ReviewReason | None   # set iff state == NEEDS_HUMAN_REVIEW
    pending_intent_id: IntentId | None   # the intent awaiting approval or execution
    action_seq: int                   # >= 0; incremented each time an action is planned
    reopen_count: int                 # >= 0
    rejection_count: int              # >= 0; approval rejections since the last GATHERING_EVIDENCE entry
    gather_round: int                 # >= 0; incremented on each GATHERING_EVIDENCE entry
    prior_case_id: CaseId | None      # a closed earlier case for the same shipment and claim
    superseded_by_case_id: CaseId | None

    @property
    def is_terminal(self) -> bool: ...
    @property
    def category(self) -> StateCategory: ...
```

Model validators (raise `DomainValidationError`): `closed_at is None` iff state is not terminal; `review_reason` set iff state is `NEEDS_HUMAN_REVIEW`; `updated_at >= opened_at`. `rejection_count` resets to 0 on every transition into `GATHERING_EVIDENCE` (a state machine effect, not a validator).

### 4.4 Inbound events and the audit event

```python
class InboundEvent(BaseModel):
    """Anything that arrives from outside the orchestrator. Submitted through Orchestrator.submit."""
    type: InboundEventType
    source: str                       # "dispute_api" | "outlook" | "seller_portal" | "jira" | "approvals" | "scheduler" | "ops_console" | "simulator"
    source_event_id: str              # unique within source; 1..256
    occurred_at: datetime
    actor: Actor
    case_id: CaseId | None = None     # known for TIMER_FIRED, APPROVAL_DECIDED and HUMAN_* events
    correlation_id: CorrelationId | None = None   # inherited where events-catalog.md says so
    payload: InboundPayload           # discriminated union on `type`; schemas in events-catalog.md

    @property
    def dedupe_key(self) -> DedupeKey:
        return DedupeKey(f"{self.source}:{self.source_event_id}")

class Event(BaseModel):
    """One row of the append-only audit trail (I8). Never updated, never deleted."""
    id: EventId
    case_id: CaseId
    seq: int                          # 1, 2, 3 ... per case; assigned by EventLog.append
    type: EventType
    occurred_at: datetime             # when it happened in the world (input) or in the kernel (internal)
    recorded_at: datetime             # when it was written
    actor: Actor
    cause: Cause
    correlation_id: CorrelationId
    causation_id: EventId | None      # the event this one was produced while handling; None only for inputs
    dedupe_key: DedupeKey | None      # set for input events only
    payload: EventPayload             # discriminated union on `type` (events-catalog.md)
    schema_version: int = 1
```

`Event.seq` is `0` when constructed by the kernel and assigned by `EventLog.append`; the returned `Event` carries the real value.

### 4.5 Evidence

```python
class EvidenceRequirement(BaseModel):
    key: str                          # pack-defined, e.g. "shipment.measured_dims"
    source: str                       # EvidenceSource.name that serves it
    required: bool                    # if True and not FOUND, the pack decides what that means in assess()

class EvidenceResult(BaseModel):
    """What one source returned for one requirement. Never invented: no value without FOUND."""
    requirement_key: str
    source: str
    status: EvidenceStatus
    value: Mapping[str, JsonValue] | None    # set iff FOUND
    untrusted: tuple[UntrustedText, ...] = ()  # free text inside the value, kept apart (I5)
    error_code: str | None = None            # set iff FAILED (e.g. "timeout", "module_unavailable:evidence")
    retrieved_at: datetime

class EvidenceItem(BaseModel):
    """An EvidenceResult once persisted."""
    id: EvidenceItemId
    case_id: CaseId
    gather_round: int
    provenance: Provenance
    result: EvidenceResult
    content_hash: PayloadHash        # payload_hash(result)

class EvidenceBundle(BaseModel):
    case_id: CaseId
    gather_round: int
    items: tuple[EvidenceItem, ...]  # every requirement of the plan appears exactly once, plus
                                     # one AI_DERIVED item per seller reply read in this round
    def by_key(self, key: str) -> EvidenceItem: ...   # raises NotFound
```

### 4.6 Assessment and facts usable in messages

```python
class Fact(BaseModel):
    """A number or value that a message may quote (I9). Messages cite facts by fact_id."""
    fact_id: str                      # stable within an assessment, e.g. "chargeable_weight"
    label: str
    value: Money | Weight | Decimal | str | int
    source_items: tuple[EvidenceItemId, ...]   # the evidence it came from (may be empty for config values)

class EligibilityResult(BaseModel):
    outcome: Literal["eligible", "ineligible", "unknown"]
    reason_codes: tuple[str, ...]     # pack-defined; non-empty unless eligible

class ChargeAssessment(BaseModel):
    """Deterministic output of DomainPack.assess. Same bundle in, same assessment out."""
    id: AssessmentId
    case_id: CaseId
    gather_round: int
    pack_version: str
    eligible: Literal["eligible", "ineligible", "unknown"]
    reason_codes: tuple[str, ...]
    charged: Money | None
    recalculated: Money | None
    discrepancy: Money | None         # |charged - recalculated|, None if either is None
    definite_errors: tuple[str, ...]  # provable billing errors, pack-defined codes
    facts: tuple[Fact, ...]           # unique fact_id
    input_hash: PayloadHash           # payload_hash of the bundle it was computed from
```

### 4.7 Briefing, proposal and other model outputs

```python
class PatternRecord(BaseModel):
    """A cross-case hypothesis (Phase 7 populates; the model exists from Phase 0)."""
    id: PatternId
    status: Literal["UNCONFIRMED", "CONFIRMED", "DISMISSED"]
    summary: str                      # <= 400 chars, produced by code or the pattern_analyst prompt
    matched_on: Mapping[str, str]     # e.g. {"hub": "HUB-07"}
    related_case_ids: tuple[CaseId, ...]
    first_seen_at: datetime
    last_seen_at: datetime

class CautionSignal(BaseModel):
    """Something that can only make the system more careful (I7, I15)."""
    kind: Literal["pattern_match", "pattern_context_unavailable", "screen_flag",
                  "repeat_dispute", "pattern_changed_recommendation"]
    min_verdict: Literal[VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE]   # never ALLOW
    reason: str                       # <= 200 chars
    source_ref: str                   # pattern id, evidence item id, prior case id ...

class CaseBriefing(BaseModel):
    """Everything the Reasoner may see. Built by code (orchestrator/briefing.py)."""
    case_id: CaseId
    state: CaseState                  # always INVESTIGATING when built
    facts: CaseFacts
    evidence: EvidenceBundle
    assessment: ChargeAssessment
    allowed_actions: tuple[ProposalAction, ...]   # DomainPack.allowed_actions minus PROPOSE_CREDIT and UPHOLD_CHARGE
                                                  # when the case has a live or verified credit (kernel rule); always
                                                  # contains ESCALATE_TO_HUMAN
    related_cases: tuple["RelatedCase", ...]       # same seller or shipment, <= 10, outcome only
    screen_results: tuple["ScreenResult", ...]
    pattern_context: tuple[PatternRecord, ...] = ()   # empty until Phase 7 (HOOK A)
    prior_rejections: tuple[str, ...] = ()           # approver notes from rejected proposals this round (trusted: staff text)
    briefing_hash: PayloadHash

class RelatedCase(BaseModel):
    case_id: CaseId; state: CaseState; closed_at: datetime | None; discrepancy: Money | None

class Finding(BaseModel):
    kind: FindingKind; statement: str; evidence_refs: tuple[str, ...]   # statement <= 400

class ActionParameters(BaseModel):
    reason_code: str | None = None
    requested_items: tuple[str, ...] = ()
    remeasurement_question: str | None = None

class PatternInfluence(BaseModel):
    considered: bool; changed_recommendation: bool; note: str   # note <= 300

class Proposal(BaseModel):
    """One structured next step (D5). Never executed by the reasoner (I1)."""
    id: ProposalId
    case_id: CaseId
    gather_round: int
    action: ProposalAction
    confidence: Confidence
    rationale: str                    # <= 800
    findings: tuple[Finding, ...]     # >= 1
    action_parameters: ActionParameters
    pattern_influence: PatternInfluence
    manipulation_flags: tuple[str, ...]
    reasoner: Literal["rules_only", "llm"]
    fallback_used: bool               # True when the rules baseline answered because reasoning was unavailable
    prompt_name: str | None           # set iff reasoner == "llm"
    prompt_version: str | None
    prompt_hash: PayloadHash | None
    model_id: str | None
    model_route: str | None           # "foundry_agent_service" | "anthropic_direct" | "fake" ...
    briefing_hash: PayloadHash
    created_at: datetime
```

Validator: `action` must be in the briefing's `allowed_actions`; the orchestrator checks this after the reasoner returns and treats a violation as `InvalidModelOutput` (escalate with `invalid_model_output`).

```python
class ReplyContext(BaseModel):
    case_id: CaseId; reply: UntrustedText; requested_items: tuple[str, ...]; facts: CaseFacts

class ReplyClaim(BaseModel):
    kind: Literal["weight", "dimensions", "date", "other"]; text: str; unit_as_written: str

class RequestedItemStatus(BaseModel):
    item: str; status: Literal["provided", "partial", "missing"]

class ReplyReading(BaseModel):   # mirrors prompts/seller_reply_interpreter/v1/schema.json
    intent: Literal["provides_requested_evidence", "partial_evidence", "disputes_again_no_new_info",
                    "withdraws_dispute", "asks_question", "irrelevant", "contains_instruction_attempt"]
    requested_items_status: tuple[RequestedItemStatus, ...]
    claims: tuple[ReplyClaim, ...]
    attachments: tuple[str, ...]
    manipulation_flags: tuple[str, ...]
    summary: str; language: str
    prompt_name: str; prompt_version: str; prompt_hash: PayloadHash; model_id: str | None

class DraftContext(BaseModel):
    case_id: CaseId; kind: MessageKind; facts: tuple[Fact, ...]; seller_ref: str
    requested_items: tuple[str, ...] = (); reason_code: str | None = None

class NumberUse(BaseModel):
    text: str; fact_id: str

class DraftedMessage(BaseModel):   # mirrors prompts/seller_message_drafter/v1/schema.json
    subject: str                      # <= 150
    body: str                         # <= 2000
    numbers_used: tuple[NumberUse, ...]
    blocked_reason: str | None
    prompt_name: str | None; prompt_version: str | None; prompt_hash: PayloadHash | None
    template_id: str | None           # set when a pack template rendered it (rules-only path)
```

### 4.8 Intents, verdicts, approvals, actions

An intent is "what we want to do, with its exact payload", created before any approval. An action is "one execution of an approved or allowed intent". Splitting them means the actions table only ever holds things that were actually started.

```python
class MessagePayload(BaseModel):
    type: Literal["message"] = "message"
    kind: MessageKind
    to: str                           # always case.facts.seller_contact; never taken from a draft or reply
    subject: str
    body: str
    numbers_used: tuple[NumberUse, ...]
    in_reply_to: str | None           # conversation id to thread under, if any

class IssuePayload(BaseModel):
    type: Literal["issue"] = "issue"
    kind: Literal["remeasurement"]
    summary: str                      # <= 200
    description: str                  # <= 4000, built from trusted facts; untrusted text quoted in a delimited block
    labels: tuple[str, ...]           # the executor adds the idempotency label itself

class CreditPayload(BaseModel):
    type: Literal["credit"] = "credit"
    shipment_ref: str
    amount: Money                     # > 0
    reason_code: str                  # from policy.credits.reason_codes
    fact_ids: tuple[str, ...]         # the assessment facts the amount is derived from (I9)

ActionPayload = Annotated[MessagePayload | IssuePayload | CreditPayload, Field(discriminator="type")]

class ActionIntent(BaseModel):
    id: IntentId
    case_id: CaseId
    source_kind: IntentSource
    source_ref: str                   # ProposalId, TimerId or the verified ActionId it follows
    action_type: ActionType
    payload: ActionPayload            # MessagePayload iff SEND_MESSAGE, IssuePayload iff UPSERT_ISSUE, CreditPayload iff POST_CREDIT
    payload_hash: PayloadHash         # payload_hash(payload)
    assessment_id: AssessmentId | None
    status: IntentStatus
    created_at: datetime

class RuleResult(BaseModel):
    rule_id: str                      # "R01" ... (A2 policy-rules.md)
    verdict: VerdictKind
    reason: str                       # <= 200, machine-friendly, e.g. "contact_limit_daily"

class PolicyVerdict(BaseModel):
    id: VerdictId
    case_id: CaseId
    intent_id: IntentId | None        # None when evaluating a proposal with no side effect (wait, escalate)
    stage: PolicyStage
    verdict: VerdictKind              # = max over rule verdicts and signal floors
    results: tuple[RuleResult, ...]   # every rule that ran, in order
    signals: tuple[CautionSignal, ...]
    review_reason: ReviewReason | None   # set iff verdict >= ESCALATE
    rules_version: str                # hash of the rule list + policy config
    evaluated_at: datetime

class ApprovalRequest(BaseModel):
    approval_id: ApprovalId
    case_id: CaseId
    intent_id: IntentId
    subject: ApprovalSubject
    payload_hash: PayloadHash
    amount: Money | None              # set iff subject == CREDIT
    message_kind: MessageKind | None  # set iff subject == MESSAGE
    requested_at: datetime
    expires_at: datetime

class Approval(BaseModel):
    """The stored approval record (I3, I13). The kernel's approvals table is the source of truth."""
    id: ApprovalId
    request: ApprovalRequest
    decision: ApprovalDecision
    decided_by: Actor | None          # HUMAN only; set iff decision in (APPROVED, REJECTED)
    decided_at: datetime | None
    note: str                         # <= 500, staff text
    used_by_action_id: ActionId | None   # set once, when an action consumes it
    notified_at: datetime | None         # when ApprovalService.request succeeded; the runner retries while null

class Action(BaseModel):
    id: ActionId
    case_id: CaseId
    intent_id: IntentId
    approval_id: ApprovalId | None    # required for POST_CREDIT (I3) and for review-required message kinds (I13)
    action_type: ActionType
    action_seq: int                   # the case.action_seq value assigned at planning (>= 1)
    idempotency_key: IdempotencyKey
    payload_hash: PayloadHash
    status: ActionStatus
    attempts: int                     # external write attempts made
    external_ref: str | None          # set from SENT onwards
    last_error_code: str | None
    planned_at: datetime
    sent_at: datetime | None
    verified_at: datetime | None
    verify_attempts: int
    verify_deadline_at: datetime | None   # sent_at + executor.verify_deadline_s
    verified_by: Actor | None             # SYSTEM, or HUMAN when a human resolved an UNKNOWN
```

### 4.9 Timers and outcomes

```python
class Timer(BaseModel):
    id: TimerId
    case_id: CaseId
    kind: TimerKind
    due_at: datetime
    anchor_at: datetime
    armed_in_state: CaseState
    armed_at_version: int
    correlation_id: CorrelationId
    created_at: datetime
    fired_at: datetime | None
    consumed_at: datetime | None      # its TIMER_FIRED was processed (not deferred)
    voided_at: datetime | None        # fired, then made stale by a cancel effect before being consumed
    cancelled_at: datetime | None
    cancel_reason: str | None

class CaseOutcome(BaseModel):
    """Sent to CaseOutcomeObserver when a case enters a terminal state (HOOK C)."""
    id: OutcomeId                     # new for every terminal entry (a reopened-then-closed case has two)
    case_id: CaseId
    state: CaseState                  # terminal
    resolved: bool                    # True only for RESOLVED_CREDITED and RESOLVED_UPHELD
    facts: CaseFacts
    assessment_id: AssessmentId | None
    credited: Money | None            # verified credit total, if any
    used_pattern_ids: tuple[PatternId, ...]   # patterns present in any briefing of this case (no circular confirmation)
    closed_at: datetime
    reopen_count: int
```

### 4.10 Screening and model calls

```python
class ScreenResult(BaseModel):
    """Advisory only (I15). A flag can only add caution; clean never relaxes anything."""
    evidence_item_id: EvidenceItemId | None
    kind: ScreenKind
    flags: tuple[str, ...]            # e.g. "instruction_like", "prompt_shield:jailbreak"
    blocked: bool                     # a provider guardrail blocked the content; the investigate step then escalates guardrail_blocked
    adapter: str

class ChatMessage(BaseModel):
    role: Literal["system", "user"]   # no "assistant" turns: every call is single-shot (ARCHITECTURE section 12)
    content: str

class RenderedPrompt(BaseModel):
    name: str; version: str; hash: PayloadHash
    messages: tuple[ChatMessage, ...]
    output_schema: Mapping[str, JsonValue]   # the JSON schema from prompts/<name>/<version>/schema.json

class ModelRequest(BaseModel):
    case_id: CaseId | None
    purpose: Literal["case_investigator", "seller_reply_interpreter", "seller_message_drafter", "pattern_analyst"]
    prompt: RenderedPrompt
    max_output_tokens: int            # <= policy.llm.max_output_tokens

class ModelUsage(BaseModel):
    input_tokens: int; output_tokens: int; latency_ms: int; cost_usd: Decimal   # cost from config/pricing.yaml

class ModelResponse(BaseModel):
    output: Mapping[str, JsonValue]   # parsed and validated against prompt.output_schema
    model_id: str
    route: str
    usage: ModelUsage
```

## 5. Errors (`domain/errors.py`)

```python
class KernelError(Exception):
    """Base. Every error carries a stable `code` string for logs and events."""
    code: ClassVar[str] = "kernel_error"

class DomainValidationError(KernelError): code = "domain_validation"
class ConfigError(KernelError): code = "config"                      # startup only; the app refuses to start
class IllegalTransition(KernelError):
    code = "illegal_transition"
    def __init__(self, state: CaseState | None, trigger: Trigger, reason: str) -> None: ...
class GuardFailed(IllegalTransition): code = "guard_failed"          # trigger exists from state, but no guard set held
class ConcurrencyConflict(KernelError):
    code = "concurrency_conflict"
    def __init__(self, case_id: CaseId, expected_version: int) -> None: ...
class NotFound(KernelError): code = "not_found"
class DuplicateEvent(KernelError):
    code = "duplicate_event"
    def __init__(self, dedupe_key: DedupeKey, existing_inbox_id: InboxId) -> None: ...
class ModuleUnavailable(KernelError):
    code = "module_unavailable"
    def __init__(self, module: str) -> None: ...                     # module = registry name
class GuardrailBlocked(KernelError): code = "guardrail_blocked"
class PolicyRefused(KernelError):
    """The executor refused because a fresh policy evaluation or approval check failed (I2, I13)."""
    code = "policy_refused"
    def __init__(self, verdict: PolicyVerdict | None, reason: str) -> None: ...
class KillSwitchEngaged(KernelError): code = "kill_switch_active"

# Errors an adapter may raise from a port method. Adapters translate every library exception.
class PortError(KernelError): code = "port_error"
class TransientPortError(PortError):
    """The request certainly did not take effect (e.g. connection refused, 429). Safe to retry."""
    code = "transient"
    def __init__(self, detail: str, retry_after_s: float | None = None) -> None: ...
class AmbiguousOutcome(PortError):
    """The request may or may not have taken effect (timeout after send, 5xx on a write)."""
    code = "ambiguous"
class PermanentPortError(PortError):
    """Retrying cannot help (400 validation, 404 on a write target, 422)."""
    code = "permanent"
    def __init__(self, detail: str, status: int | None = None) -> None: ...
class AuthPortError(PermanentPortError): code = "auth"            # 401/403; also logged at ERROR level
class ExternalNotFound(PortError): code = "external_not_found"    # read-back target not (yet) visible
class InvalidModelOutput(PortError): code = "invalid_model_output"   # after the one permitted retry
class BudgetExceeded(PortError):
    code = "budget_exceeded"
    def __init__(self, kind: Literal["llm_budget_exceeded", "credit_cap_reached"]) -> None: ...
class AgentDrift(PortError): code = "agent_drift"
```

Error mapping inside the orchestrator (one place, `orchestrator/errors.py`): `ModuleUnavailable(m)` -> escalate `module_unavailable:m`; `GuardrailBlocked` -> `guardrail_blocked`; `BudgetExceeded(k)` -> `k`; `AgentDrift` -> `agent_drift`; `InvalidModelOutput` -> `invalid_model_output`; `KillSwitchEngaged` -> `kill_switch_active`; `PolicyRefused` -> `policy_denied`; any other `KernelError` or unexpected exception inside a step -> `internal_error` (logged with traceback; the case still gets a reason). `ConcurrencyConflict` is never mapped to a review: it is retried (orchestrator.md section 4).

## 6. Ports (`ports/`, one file per port)

All ports are `typing.Protocol` classes. Persistence ports are implemented in `infra/sqlite`; module ports by adapters in `modules/`. Every module port has a `Disabled` adapter whose every method raises `ModuleUnavailable(<module>)`.

### 6.1 Infrastructure ports

```python
class Clock(Protocol):
    def now(self) -> datetime:
        """Current UTC time, timezone-aware. Post: monotonic non-decreasing within a process
        (the system adapter clamps backwards jumps). Raises nothing."""

class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str:
        """Pre: prefix in ID_PREFIXES. Post: globally unique, matches ^{prefix}_[0-9A-Z]{26}$.
        Raises DomainValidationError for an unknown prefix."""

class KillSwitch(Protocol):
    def is_engaged(self) -> bool:
        """True if external writes must stop (I10). Read fresh on every call (no caching):
        engaged if the KILL_SWITCH setting is true OR control_flags.kill_switch is '1'.
        If the flag cannot be read, returns True (fail safe). Raises nothing."""

class ModuleStatus(Protocol):
    def is_enabled(self, module: str) -> bool:
        """From the registry built at startup. Pre: module is a registered name.
        Raises ConfigError for an unknown name (a programming error)."""
    def adapter_name(self, module: str) -> str: ...

class Tracer(Protocol):
    def span(self, name: str, **attrs: str) -> AbstractContextManager[None]:
        """No content attributes unless telemetry.capture_content (I14). Never raises; a broken
        tracer degrades to no-op."""
```

### 6.2 Persistence ports (used only inside a unit of work)

A unit of work is one database transaction. Everything the kernel writes for one step (case row, events, intents, actions, timers, inbox status) commits together or not at all.

```python
class UnitOfWork(Protocol):
    cases: "CaseRepository"
    events: "EventLog"
    inbox: "InboxStore"
    evidence: "EvidenceStore"
    assessments: "AssessmentStore"
    proposals: "ProposalStore"
    intents: "IntentStore"
    verdicts: "VerdictStore"
    approvals: "ApprovalStore"
    actions: "ActionStore"
    messages: "MessageStore"
    timers: "TimerStore"
    outcomes: "OutcomeOutbox"
    def __enter__(self) -> "UnitOfWork":
        """Begins a transaction (SQLite: BEGIN IMMEDIATE, so the write lock is taken up front)."""
    def __exit__(self, exc_type, exc, tb) -> None:
        """Commits if no exception, else rolls back. A commit failure raises ConcurrencyConflict
        if caused by a version check, else re-raises."""

UnitOfWorkFactory = Callable[[], UnitOfWork]

class CaseRepository(Protocol):
    def get(self, case_id: CaseId) -> Case:
        """Raises NotFound."""
    def insert(self, case: Case) -> Case:
        """Pre: case.version == 1. Raises ConcurrencyConflict if another open case exists for the
        same (shipment_ref, claim_ref) (unique partial index)."""
    def save(self, case: Case, expected_version: int) -> Case:
        """UPDATE ... WHERE id = ? AND version = expected_version. Post: returned case has
        version == expected_version + 1. Raises ConcurrencyConflict if 0 rows matched, NotFound
        if the id does not exist."""
    def find_open(self, shipment_ref: str, claim_ref: str) -> Case | None:
        """The case for this pair in a working state, if any (at most one by schema)."""
    def find_latest(self, shipment_ref: str, claim_ref: str) -> Case | None:
        """The most recently opened case for the pair, in any state."""
    def list_in_states(self, states: Collection[CaseState], limit: int = 500) -> Sequence[Case]:
        """For restart recovery. Ordered by updated_at ascending."""
    def related(self, case: Case, limit: int = 10) -> Sequence[RelatedCase]:
        """Other cases with the same seller_ref or shipment_ref, newest first, excluding case."""

class EventLog(Protocol):
    """Append-only (I8). There is deliberately no update or delete method."""
    def append(self, event: Event) -> Event:
        """Assigns seq = last seq for the case + 1. Raises DuplicateEvent if event.dedupe_key is set
        and already used; NotFound if the case does not exist."""
    def list_for_case(self, case_id: CaseId, after_seq: int = 0) -> Sequence[Event]:
        """Ordered by seq ascending."""
    def get(self, event_id: EventId) -> Event: ...   # raises NotFound

class InboxStore(Protocol):
    def accept(self, event: InboundEvent, received_at: datetime) -> InboxId:
        """Insert with status PENDING. Raises DuplicateEvent(dedupe_key, existing id) on a repeat."""
    def get(self, inbox_id: InboxId) -> "InboxEntry": ...
    def mark(self, inbox_id: InboxId, status: InboxStatus, *, case_id: CaseId | None = None,
             event_id: EventId | None = None, error: str | None = None) -> None:
        """Pre: legal status move (PENDING -> any; DEFERRED -> PROCESSED | DEFERRED | PENDING (retry after a
        conflict); FAILED -> PENDING (operator retry)). Raises DomainValidationError otherwise."""
    def deferred_for_case(self, case_id: CaseId) -> Sequence["InboxEntry"]:
        """Oldest first (received_at, then id)."""
    def pending(self, limit: int = 500) -> Sequence["InboxEntry"]: ...

class InboxEntry(BaseModel):
    id: InboxId; event: InboundEvent; received_at: datetime; status: InboxStatus
    case_id: CaseId | None; event_id: EventId | None; attempts: int; last_error: str | None

class EvidenceStore(Protocol):
    def add(self, item: EvidenceItem) -> None: ...
    def bundle(self, case_id: CaseId, gather_round: int) -> EvidenceBundle:
        """Raises NotFound if the round has no items."""
    def unread_replies(self, case_id: CaseId) -> Sequence["MessageRecord"]:
        """Inbound seller messages not yet interpreted in any gather round."""

class AssessmentStore(Protocol):
    def add(self, assessment: ChargeAssessment) -> None: ...
    def latest(self, case_id: CaseId) -> ChargeAssessment | None: ...

class ProposalStore(Protocol):
    def add(self, proposal: Proposal) -> None: ...
    def get(self, proposal_id: ProposalId) -> Proposal: ...
    def pattern_ids_used(self, case_id: CaseId) -> tuple[PatternId, ...]: ...

class IntentStore(Protocol):
    def add(self, intent: ActionIntent) -> None: ...
    def get(self, intent_id: IntentId) -> ActionIntent: ...
    def set_status(self, intent_id: IntentId, status: IntentStatus) -> None: ...

class VerdictStore(Protocol):
    def add(self, verdict: PolicyVerdict) -> None: ...

class ApprovalStore(Protocol):
    def add(self, request: ApprovalRequest) -> Approval:
        """Creates a PENDING approval. Raises DomainValidationError if a PENDING approval already
        exists for the intent."""
    def get(self, approval_id: ApprovalId) -> Approval: ...
    def decide(self, approval_id: ApprovalId, decision: ApprovalDecision, by: Actor | None,
               at: datetime, note: str) -> Approval:
        """Pre: current decision is PENDING; by.kind == HUMAN for APPROVED/REJECTED.
        Raises DomainValidationError otherwise."""
    def consume(self, approval_id: ApprovalId, action_id: ActionId) -> None:
        """Sets used_by_action_id. Raises DomainValidationError if not APPROVED or already used."""
    def unnotified(self) -> Sequence[Approval]: ...          # PENDING with notified_at null
    def mark_notified(self, approval_id: ApprovalId, at: datetime) -> None: ...

class ActionStore(Protocol):
    def add(self, action: Action) -> None:
        """Raises DuplicateEvent if the idempotency key or intent already has an action."""
    def get(self, action_id: ActionId) -> Action: ...
    def update(self, action: Action) -> None:
        """Pre: legal status move (executor.md section 1). Raises DomainValidationError otherwise."""
    def in_flight(self) -> Sequence[Action]: ...               # PLANNED or SENT, for recovery
    def for_case(self, case_id: CaseId) -> Sequence[Action]: ...
    def outbound_message_count(self, case_id: CaseId, since: datetime | None = None) -> int:
        """SEND_MESSAGE actions in SENT, VERIFIED or UNKNOWN (UNKNOWN counts: we may have contacted the seller)."""
    def live_credits(self, shipment_ref: str, reason_code: str) -> Sequence[Action]:
        """POST_CREDIT actions in PLANNED, SENT, UNKNOWN or VERIFIED for this shipment and reason, across
        all cases (I3). FAILED credits are excluded: they are proven not applied."""
    def mark_in_flight_unknown(self, case_id: CaseId, at: datetime) -> Sequence[Action]:
        """PLANNED/SENT -> UNKNOWN with reason in_flight_at_escalation (T90 effect). Returns them."""

class MessageStore(Protocol):
    def add(self, record: "MessageRecord") -> None: ...
    def case_for_conversation(self, conversation_id: str) -> CaseId | None: ...

class MessageRecord(BaseModel):
    id: MessageId; case_id: CaseId; direction: Literal["OUT", "IN"]
    action_id: ActionId | None; inbox_id: InboxId | None
    channel: str; external_message_id: str; conversation_id: str | None
    from_addr: str; to_addr: str; subject: str; body_hash: PayloadHash
    kind: MessageKind | None; at: datetime; interpreted_round: int | None

class TimerStore(Protocol):
    def schedule(self, timer: Timer) -> None:
        """Raises DomainValidationError if an active timer of the same kind exists for the case."""
    def cancel(self, case_id: CaseId, kinds: Collection[TimerKind] | None, at: datetime, reason: str) -> Sequence[Timer]:
        """kinds None = all. Cancels active timers of those kinds AND voids fired-but-unconsumed ones
        (their deferred TIMER_FIRED becomes stale). Returns the timers cancelled or voided."""
    def active(self, case_id: CaseId) -> Sequence[Timer]: ...
    def due(self, now: datetime, limit: int = 200) -> Sequence[Timer]:
        """Active timers with due_at <= now, oldest first."""
    def mark_fired(self, timer_id: TimerId, at: datetime) -> None:
        """Raises DomainValidationError if already fired or cancelled."""
    def consume(self, timer_id: TimerId, at: datetime) -> None:
        """Sets consumed_at when its TIMER_FIRED is processed with any reaction other than defer."""
    def is_stale(self, timer_id: TimerId, case_id: CaseId) -> bool:
        """Pre-dispatch rule P1: cancelled, voided, consumed, or belongs to another case."""
    def get(self, timer_id: TimerId) -> Timer: ...

class OutcomeOutbox(Protocol):
    def add(self, outcome: CaseOutcome) -> None: ...
    def undelivered(self, limit: int = 100) -> Sequence[CaseOutcome]: ...
    def mark_delivered(self, outcome_id: OutcomeId, at: datetime) -> None: ...
    def record_failure(self, outcome_id: OutcomeId, error: str) -> None: ...
```

### 6.3 Domain pack port

```python
class DomainPack(Protocol):
    name: str
    version: str
    def parse_facts(self, raw: Mapping[str, JsonValue]) -> Mapping[str, JsonScalar]:
        """Validates pack_facts at intake. Raises DomainValidationError."""
    def eligibility(self, facts: CaseFacts) -> EligibilityResult:
        """Pure. 'unknown' when facts are insufficient (the case escalates: eligibility_unknown)."""
    def evidence_plan(self, facts: CaseFacts) -> Sequence[EvidenceRequirement]:
        """Pure. Non-empty. Each requirement names a source registered with the evidence service."""
    def assess(self, case_id: CaseId, gather_round: int, bundle: EvidenceBundle) -> ChargeAssessment:
        """Pure and deterministic (I9): equal bundles give equal assessments. Never raises for
        missing evidence; it reports it."""
    def allowed_actions(self, state: CaseState, assessment: ChargeAssessment) -> Sequence[ProposalAction]:
        """Pure. Always includes ESCALATE_TO_HUMAN."""
    def baseline_proposal(self, briefing: CaseBriefing) -> Proposal:
        """The rules-only reasoner (FLOW section 5): decides clear cases, otherwise returns
        ESCALATE_TO_HUMAN. Used as the reasoning module's rules_only adapter and as the fallback."""
    def render_template(self, ctx: DraftContext) -> DraftedMessage:
        """Deterministic message text for the rules-only path."""
    def policy_defaults(self) -> Mapping[str, JsonValue]: ...
```

### 6.4 Module ports

```python
class EvidenceSource(Protocol):
    name: str
    def fetch(self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement) -> EvidenceResult:
        """Read-only. Never invents: returns FOUND with a value, MISSING, or FAILED with error_code.
        Raises ModuleUnavailable only (every other failure is a FAILED result). Timeout <= 10 s."""

class Reasoner(Protocol):
    def propose(self, briefing: CaseBriefing) -> Proposal:
        """Receives read-only data only (I1): the briefing. Has no reference to any store, channel
        or executor. Raises ModuleUnavailable, InvalidModelOutput, BudgetExceeded, GuardrailBlocked,
        AgentDrift."""

class ReplyInterpreter(Protocol):
    def interpret(self, ctx: ReplyContext) -> ReplyReading:
        """Output is data only. Same raises as Reasoner.propose."""

class MessageDrafter(Protocol):
    def draft(self, ctx: DraftContext) -> DraftedMessage:
        """Same raises as Reasoner.propose. A non-null blocked_reason means 'do not send'."""

class InputScreen(Protocol):
    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult:
        """Advisory (I15). Raises ModuleUnavailable only; any other failure returns a result with
        flags=("screen_error",), which adds caution."""

class ModelGateway(Protocol):
    def complete(self, req: ModelRequest) -> ModelResponse:
        """The only path to a model (I14). Validates output against the schema with one retry.
        Raises ModuleUnavailable, TransientPortError, InvalidModelOutput, BudgetExceeded,
        GuardrailBlocked, AgentDrift."""

class PromptRegistry(Protocol):
    def load(self, name: str, version: str, variables: Mapping[str, JsonValue],
             untrusted: Mapping[str, UntrustedText]) -> RenderedPrompt:
        """Renders prompts/<name>/<version>/; untrusted values only inside delimited blocks.
        Raises NotFound, DomainValidationError."""

# --- write ports: every one has a write, a lookup by idempotency key, and a read-back ---

class MessageChannel(Protocol):
    idempotent_writes: bool           # True if send() with a reused key cannot send twice
    lookup_supported: bool            # True if find_sent() works
    def send(self, msg: "OutboundMessage", key: IdempotencyKey) -> "SendReceipt":
        """Raises TransientPortError, AmbiguousOutcome, PermanentPortError, AuthPortError, ModuleUnavailable.
        Timeout: executor.call_timeout_s."""
    def find_sent(self, key: IdempotencyKey) -> "SendReceipt | None":
        """The message previously sent with this key, if visible. Raises TransientPortError."""
    def get_message(self, receipt: "SendReceipt") -> "MessageSnapshot":
        """Read-back. Raises ExternalNotFound (not yet visible), TransientPortError."""
    def fetch_replies(self, since: datetime) -> Sequence["InboundMessage"]:
        """Polling. Raises TransientPortError, AuthPortError, ModuleUnavailable."""

class TicketingSystem(Protocol):
    idempotent_writes: bool
    lookup_supported: bool
    def upsert_issue(self, spec: "IssueSpec", key: IdempotencyKey) -> "IssueRef": ...      # raises as send()
    def find_issue(self, key: IdempotencyKey) -> "IssueRef | None": ...
    def get_issue(self, ref: "IssueRef") -> "IssueSnapshot": ...                           # raises as get_message()
    def fetch_findings(self, since: datetime) -> Sequence["OpsFinding"]: ...

class Ledger(Protocol):
    idempotent_writes: bool           # the simulated ledger: True
    lookup_supported: bool
    def post_credit(self, req: "CreditRequest", key: IdempotencyKey) -> "TransactionRef": ...
    def find_transaction(self, key: IdempotencyKey) -> "TransactionRef | None": ...
    def get_transaction(self, ref: "TransactionRef") -> "TransactionSnapshot": ...
    def get_entries(self, shipment_ref: str) -> Sequence["LedgerEntry"]: ...

class ApprovalService(Protocol):
    """Notifies the review queue. The decision comes back as an APPROVAL_DECIDED inbound event;
    the kernel's approvals table is the record (ADR 0013)."""
    def request(self, approval: Approval) -> None:
        """Idempotent on approval.id. Raises ModuleUnavailable, TransientPortError."""
    def withdraw(self, approval_id: ApprovalId, reason: str) -> None:
        """Idempotent. Raises ModuleUnavailable, TransientPortError."""

class PatternContextProvider(Protocol):          # HOOK A (read side)
    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        """Read-only. Null adapter returns (). Raises TransientPortError (becomes a
        pattern_context_unavailable caution signal, never a silent skip)."""

class CaseOutcomeObserver(Protocol):             # HOOK C (write side)
    def on_outcome(self, outcome: CaseOutcome) -> None:
        """Called after commit, at least once per outcome id: must be idempotent on outcome.id.
        Its failure never affects the case. Null adapter does nothing."""
```

Write-port data models:

```python
class OutboundMessage(BaseModel):
    to: str; subject: str; body: str; in_reply_to: str | None
    case_id: CaseId; kind: MessageKind

class SendReceipt(BaseModel):
    external_message_id: str; conversation_id: str | None; sent_at: datetime
    duplicate: bool = False           # True if the adapter returned an earlier send for this key

class MessageSnapshot(BaseModel):
    external_message_id: str; to: tuple[str, ...]; subject: str
    body: str                         # plain text exactly as sent; the executor hashes it (executor.md section 6)
    idempotency_key: IdempotencyKey | None; in_sent_items: bool; conversation_id: str | None

class InboundMessage(BaseModel):
    external_message_id: str; internet_message_id: str; conversation_id: str | None
    from_addr: str; to_addr: str; subject: UntrustedText; body: UntrustedText
    received_at: datetime; attachments: tuple["AttachmentRef", ...]
    is_delivery_failure: bool; failed_message_id: str | None

class AttachmentRef(BaseModel):
    name: str; content_type: str; size_bytes: int; content_hash: PayloadHash

class IssueSpec(BaseModel):
    project_key: str; summary: str; description: str; labels: tuple[str, ...]; case_id: CaseId

class IssueRef(BaseModel):
    key: str; id: str; duplicate: bool = False

class IssueSnapshot(BaseModel):
    key: str; project_key: str; labels: tuple[str, ...]; summary: str
    description_hash: PayloadHash; status: str

class OpsFinding(BaseModel):
    issue_key: str; finding_id: str; kind: Literal["remeasured", "inspected", "no_fault_found"]
    measured: Mapping[str, JsonScalar]; note: UntrustedText | None; at: datetime

class CreditRequest(BaseModel):
    shipment_ref: str; amount: Money; reason_code: str; case_id: CaseId

class TransactionRef(BaseModel):
    transaction_id: str; duplicate: bool = False

class TransactionSnapshot(BaseModel):
    transaction_id: str; shipment_ref: str; amount: Money; reason_code: str
    type: Literal["CREDIT", "DEBIT"]; status: Literal["POSTED", "PENDING", "REVERSED"]
    idempotency_key: IdempotencyKey; posted_at: datetime | None

class LedgerEntry(BaseModel):
    transaction_id: str; shipment_ref: str; type: Literal["CREDIT", "DEBIT"]
    amount: Money; reason_code: str; posted_at: datetime
```

## 7. Policy engine (`policy/`)

The rule catalogue is Stage A2. A1 fixes the shape and the composition law, because the state machine and executor depend on it.

```python
class PolicyContext(BaseModel):
    case: Case
    stage: PolicyStage
    intent: ActionIntent | None
    proposal: Proposal | None
    assessment: ChargeAssessment | None
    approval: Approval | None          # EXECUTION stage only
    signals: tuple[CautionSignal, ...]
    modules: Mapping[str, bool]        # snapshot of ModuleStatus
    kill_switch: bool
    messages_sent_total: int
    messages_sent_today: int           # since 00:00 Asia/Kolkata (policy.contact.day_boundary_tz)
    live_credits_same_shipment_reason: int   # ActionStore.live_credits(...), across all cases
    case_has_verified_credit: bool
    screen_results: tuple[ScreenResult, ...]
    reply_readings: tuple[ReplyReading, ...]
    policy: "PolicyConfig"             # parsed config/policy.yaml; minimal shape below, A3 config.md completes it
    now: datetime

class TimersConfig(BaseModel):
    reminder_after_hours: int = 48; decision_after_hours: int = 72
    reopen_window_days: int = 7; ops_timeout_hours: int = 120
class ContactConfig(BaseModel):
    max_messages_per_case: int = 3; max_messages_per_day: int = 1; day_boundary_tz: str = "Asia/Kolkata"
class MessagesConfig(BaseModel):
    review_required_kinds: frozenset[MessageKind] = frozenset(MessageKind)   # pilot: all (D6)
    auto_send_kinds: frozenset[MessageKind] = frozenset()                    # validator: disjoint, union = all kinds
    allowed_recipients: frozenset[str]                                       # test addresses only (I11)
class CreditsConfig(BaseModel):
    require_approval: Literal[True] = True                                   # cannot be configured off (I3)
    reason_codes: frozenset[str] = frozenset({"calculation_error", "remeasurement_lower", "duplicate_charge"})
class ApprovalsConfig(BaseModel):
    expire_after_hours: int = 72; max_rejections_per_case: int = 2
class CasesConfig(BaseModel):
    max_agent_reopens: int = 2
class PatternsConfig(BaseModel):
    min_related_cases: int = 3; window_days: int = 14
    match_min_verdict: Literal[VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE] = VerdictKind.REQUIRE_APPROVAL

class PolicyConfig(BaseModel):
    """The kernel-facing part of config/policy.yaml. Frozen; loaded once at startup by infra/settings.
    A3 config.md adds llm, llm_primary, llm_secondary and telemetry sections and owns final defaults."""
    timers: TimersConfig; contact: ContactConfig; messages: MessagesConfig; credits: CreditsConfig
    approvals: ApprovalsConfig; cases: CasesConfig; patterns: PatternsConfig

class PolicyRule(Protocol):
    rule_id: str
    def evaluate(self, ctx: PolicyContext) -> RuleResult:
        """Pure. Must not raise for valid input; a bug is caught by the engine and becomes
        DENY with reason 'rule_error:<rule_id>' (fail safe)."""

class PolicyEngine:
    def __init__(self, rules: Sequence[PolicyRule], clock: Clock, ids: IdGenerator) -> None: ...
    def evaluate(self, ctx: PolicyContext) -> PolicyVerdict:
        """verdict = max(ALLOW, every rule verdict, every signal.min_verdict).
        Post: verdict >= every signal.min_verdict (I7, I15); review_reason set iff verdict >= ESCALATE."""
```

Composition law (the property Phase 1 tests with hypothesis): the verdict is the maximum of its inputs on the ordering `ALLOW < REQUIRE_APPROVAL < ESCALATE < DENY`. Adding a signal or a rule result can only raise the verdict. Because `CautionSignal.min_verdict` cannot be `ALLOW` (type-level), no signal can lower anything.

Verdict to trigger mapping (in the investigate, reminder, verify and hand_off steps):

| Verdict | Trigger | Notes |
|---------|---------|-------|
| ALLOW | `verdict_allow` / `reminder_allow` / `follow_up_allow` | executor.plan runs in the same transaction |
| REQUIRE_APPROVAL | `..._require_approval` | approval row created; requires `approvals` enabled, else the rule set returns DENY with `module_unavailable:approvals` |
| ESCALATE or DENY | `escalate` (review_reason from verdict) | reminder step: `rest` + REMINDER_SKIPPED instead; hand_off step: `follow_up_blocked` |

## 8. State machine (`statemachine/`)

```python
class TransitionSpec(BaseModel):
    id: str; from_state: CaseState | None; to_state: CaseState; trigger: Trigger
    guards: tuple[str, ...]; effects: "Effects"

class Effects(BaseModel):
    cancel_timers: Literal["all"] | tuple[TimerKind, ...] = ()
    schedule_timers: tuple[TimerKind, ...] = ()
    supersede_pending: bool = False; clear_pending: bool = False
    set_review_reason: bool = False; clear_review_reason: bool = False
    increment: tuple[Literal["reopen_count", "rejection_count"], ...] = ()
    record_outcome: bool = False

class GuardContext(BaseModel):
    """Everything a guard may read. Built by the orchestrator; guards are pure functions of it."""
    case: Case; now: datetime; event: InboundEvent | None
    pending_intent: ActionIntent | None; approval: Approval | None
    verified_kind: VerifiedKind | None; action_planned: bool
    active_timers: tuple[Timer, ...]; actions: tuple[Action, ...]
    policy: "PolicyConfig"

Guard = Callable[[GuardContext], bool]

class TransitionResult(BaseModel):
    case: Case                        # new state, counters, timestamps; version unchanged (repository bumps it)
    spec: TransitionSpec
    events: tuple[Event, ...]         # STATE_CHANGED first, then effect events (TIMER_*, APPROVAL_SUPERSEDED, CASE_OUTCOME_RECORDED)
    timer_commands: tuple["TimerCommand", ...]
    outcome: CaseOutcome | None

class StateMachine:
    def __init__(self, table: Sequence[TransitionSpec], guards: Mapping[str, Guard],
                 accept: Mapping[CaseState, Mapping[str, str]], clock: Clock, ids: IdGenerator) -> None:
        """Validates the table at construction (checks C1-C14). Raises ConfigError."""
    def reaction(self, state: CaseState, event: InboundEvent) -> tuple[Reaction, str | None]:
        """From the accept matrix; key is the event type, or 'TIMER_FIRED.<kind>' for timers.
        Returns (TRANSITION, trigger) / (STEP, step name) / (ABSORB|DEFER|IGNORE, None).
        For 'a | b' entries returns the first and the orchestrator falls back to the second on GuardFailed."""
    def decide(self, case: Case, trigger: Trigger, ctx: GuardContext,
               review_reason: ReviewReason | None = None) -> TransitionSpec:
        """Pure. Returns the unique transition from case.state on trigger whose guards all hold.
        Raises IllegalTransition (no transition for the trigger), GuardFailed (none whose guards hold),
        DomainValidationError (escalate without review_reason; or more than one match, a table bug)."""
    def apply(self, case: Case, spec: TransitionSpec, ctx: GuardContext, cause: Cause,
              correlation_id: CorrelationId, causation_id: EventId | None,
              review_reason: ReviewReason | None = None) -> TransitionResult:
        """Pure: computes the new case and the events. Writes nothing. Post: exactly one
        STATE_CHANGED event whose payload names from, to, trigger, transition id and cause (I8)."""
```

Nothing writes `case.state` except the orchestrator persisting a `TransitionResult`. `CaseRepository.save` additionally checks (as a defensive assertion in the SQLite adapter) that `(old_state, new_state)` is a pair present in the table, so a direct write that skipped the machine fails (I8).

## 9. Executor (`executor/`)

Full lifecycle in `executor.md`. Signatures:

```python
class ExecutionOutcome(BaseModel):
    kind: Literal["sent", "failed", "unknown", "blocked"]
    action: Action                    # the updated action (not yet persisted; the orchestrator persists it)
    error_code: str | None

class VerificationOutcome(BaseModel):
    kind: Literal["verified", "pending", "failed"]
    action: Action
    verified_kind: VerifiedKind | None    # set iff verified
    checks: tuple[str, ...]               # names of the checks that passed or failed
    next_attempt_at: datetime | None      # set iff pending

class ActionExecutor:
    def __init__(self, policy: PolicyEngine, channels: "WritePorts", kill_switch: KillSwitch,
                 clock: Clock, ids: IdGenerator, settings: "ExecutorSettings", tracer: Tracer) -> None: ...
    def plan(self, uow: UnitOfWork, case: Case, intent: ActionIntent, ctx: PolicyContext) -> Action:
        """Inside the caller's transaction. Re-evaluates policy at stage EXECUTION; refuses unless
        the verdict is ALLOW, or REQUIRE_APPROVAL with a matching APPROVED, unused approval
        (I2, I3, I13). Writes the PLANNED action, consumes the approval, sets intent PLANNED,
        increments case.action_seq (returned case update is the caller's to save).
        Raises PolicyRefused, KillSwitchEngaged, ModuleUnavailable."""
    def execute(self, action: Action) -> ExecutionOutcome:
        """Outside any transaction. Checks the kill switch first (I10) and re-checks the approval
        binding. Looks up by key before writing. Never raises for port errors: they become outcomes."""
    def verify(self, action: Action) -> VerificationOutcome:
        """Independent read-back (I2). Never raises for port errors."""

class WritePorts(BaseModel):          # arbitrary_types_allowed
    messages: MessageChannel; tickets: TicketingSystem; ledger: Ledger
```

## 10. Orchestrator (`orchestrator/`)

Algorithm in `orchestrator.md`. Signatures:

```python
class SubmitResult(BaseModel):
    inbox_id: InboxId; duplicate: bool; case_id: CaseId | None; final_state: CaseState | None

class Orchestrator:
    def __init__(self, deps: "OrchestratorDeps") -> None: ...
    def submit(self, event: InboundEvent) -> SubmitResult:
        """Accept into the inbox (dedupe), dispatch, advance. A duplicate returns the original
        inbox id with duplicate=True and does nothing else."""
    def dispatch(self, inbox_id: InboxId) -> CaseId | None: ...
    def advance(self, case_id: CaseId, correlation_id: CorrelationId, causation_id: EventId | None) -> CaseState: ...
    def recover(self) -> "RecoveryReport": ...

class TimerScheduler:
    def fire_due(self) -> Sequence[InboxId]:
        """For each due timer: one transaction marks it fired and inserts a TIMER_FIRED inbox row
        (dedupe 'scheduler:<timer_id>'). Then each is dispatched through the orchestrator."""

class OutcomeDispatcher:
    def deliver_pending(self) -> int:
        """Delivers undelivered outcome_outbox rows to CaseOutcomeObserver (HOOK C)."""

class OrchestratorDeps(BaseModel):    # arbitrary_types_allowed; all constructor-injected (no globals)
    uow: UnitOfWorkFactory; machine: StateMachine; policy: PolicyEngine; executor: ActionExecutor
    pack: DomainPack; evidence_sources: Mapping[str, EvidenceSource]; reasoner: Reasoner
    reply_interpreter: ReplyInterpreter; drafter: MessageDrafter; input_screen: InputScreen
    pattern_context: PatternContextProvider; approvals: ApprovalService; modules: ModuleStatus
    outcomes: OutcomeDispatcher; clock: Clock; ids: IdGenerator; tracer: Tracer
    policy_config: "PolicyConfig"; settings: "OrchestratorSettings"
```

## 11. Settings introduced by A1

These keys are new. A3 (`config.md`) owns their final schema; the defaults below apply until then. All values are illustrative (D10).

| Key | Default | Used by |
|-----|---------|---------|
| `policy.timers.ops_timeout_hours` | 120 | OPS_TIMEOUT |
| `policy.cases.max_agent_reopens` | 2 | guard `reopen_cap_ok` |
| `policy.approvals.expire_after_hours` | 72 | APPROVAL_EXPIRY |
| `policy.approvals.max_rejections_per_case` | 2 | guard `rejection_cap_ok` |
| `policy.contact.day_boundary_tz` | `Asia/Kolkata` | daily contact limit |
| `orchestrator.max_steps_per_advance` | 12 | step limit |
| `orchestrator.concurrency_retries` | 3 | conflict retries |
| `executor.call_timeout_s` | 10 | every external call |
| `executor.max_attempts` | 3 | write retries |
| `executor.backoff_base_s` / `backoff_factor` / `backoff_cap_s` | 1 / 2 / 8 | retry backoff, full jitter |
| `executor.lookup_attempts` | 3 | lookup-by-key after ambiguous outcome |
| `executor.verify_schedule_s` | `[0, 5, 30, 120, 300]` | VERIFY_RETRY |
| `executor.verify_deadline_s` | 600 | verification deadline |
| `executor.execute_budget_s` / `claim_lease_s` | 240 / 300 | bound on one execute run; action lease (must exceed the budget) |

## 12. Differences from the ARCHITECTURE.md section 4 sketch

| Sketch | Spec | Why |
|--------|------|-----|
| `EventLog.append(event) -> None` | returns the `Event` with `seq` | the per-case sequence is needed for ordering and for `causation_id` links |
| `CaseRepository` has `get`, `save` only | adds `insert`, `find_open`, `find_latest`, `list_in_states`, `related` | intake dedupe, restart recovery and briefings need them |
| No unit of work | `UnitOfWork` with one store per table | a step's writes must commit together (ADR 0004) |
| `MessageChannel`, `TicketingSystem`, `Ledger` have write and read-back | each adds `find_*` by idempotency key and the `idempotent_writes` / `lookup_supported` flags | safe retry and crash recovery without double-sending (ADR 0007) |
| `ApprovalService.request -> ApprovalId`, `get -> ApprovalStatus` | `request(approval) -> None`, `withdraw`; decisions arrive as events | one source of truth: the kernel approvals table (ADR 0013) |
| `EvidenceSource.fetch(ref)` | `fetch(case_id, facts, requirement)` | a source serves named requirements from the pack's plan |
| `PromptRegistry.load(name, version)` | adds `variables` and `untrusted` | untrusted text is passed separately so it can only land in delimited blocks (I5) |
| `DomainPack` | adds `version`, `parse_facts`, `baseline_proposal`, `render_template`; `assess` takes the case id and round | the rules-only reasoner and templates are domain knowledge; assessments are stored per round |
| No `KillSwitch`, `ModuleStatus` ports | added | I10 needs a fresh read inside the executor; I6 needs module state in policy |
