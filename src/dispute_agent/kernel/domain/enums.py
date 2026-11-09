"""Enumerations (kernel-interfaces.md section 3).

All StrEnum except VerdictKind, which is an IntEnum because verdicts are ordered by caution
and combining them takes the max. Values are written explicitly, never with auto(), because
they are the strings persisted in SQLite and listed in schema.sql CHECK constraints.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class CaseState(StrEnum):
    RECEIVED = "RECEIVED"
    GATHERING_EVIDENCE = "GATHERING_EVIDENCE"
    CALCULATING = "CALCULATING"
    INVESTIGATING = "INVESTIGATING"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    AWAITING_SELLER = "AWAITING_SELLER"
    AWAITING_OPS = "AWAITING_OPS"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED_CREDITED = "RESOLVED_CREDITED"
    RESOLVED_UPHELD = "RESOLVED_UPHELD"
    WITHDRAWN = "WITHDRAWN"
    CLOSED_NO_RESPONSE = "CLOSED_NO_RESPONSE"
    REJECTED_INELIGIBLE = "REJECTED_INELIGIBLE"
    HANDED_TO_HUMAN = "HANDED_TO_HUMAN"


# Transcribed from the `category` and `resolved` keys in transitions.yaml, which stays the
# authority; test_enums.py reads the file and fails if these two drift from it. They live
# here rather than behind the Phase 1 loader because a domain model needs them at import
# time, and the kernel reads no files.
TERMINAL_STATES: frozenset[CaseState] = frozenset(
    {
        CaseState.RESOLVED_CREDITED,
        CaseState.RESOLVED_UPHELD,
        CaseState.WITHDRAWN,
        CaseState.CLOSED_NO_RESPONSE,
        CaseState.REJECTED_INELIGIBLE,
        CaseState.HANDED_TO_HUMAN,
    }
)

# "Resolved" means the dispute was answered on its merits. A withdrawal, a silence, an
# ineligible claim and a hand-off all close the case without ever settling the question, and
# the difference is what the Phase 6 resolution-rate metric counts.
RESOLVED_STATES: frozenset[CaseState] = frozenset(
    {CaseState.RESOLVED_CREDITED, CaseState.RESOLVED_UPHELD}
)


class StateCategory(StrEnum):
    AUTO = "AUTO"
    AUTO_MAY_REST = "AUTO_MAY_REST"
    WAITING = "WAITING"
    HUMAN = "HUMAN"
    TERMINAL = "TERMINAL"


#: Every state's category, transcribed from transitions.yaml and drift-tested against it.
#: `Case.category` reads this; the Phase 1 transition loader reads the file itself.
STATE_CATEGORY: dict[CaseState, StateCategory] = {
    CaseState.RECEIVED: StateCategory.AUTO,
    CaseState.GATHERING_EVIDENCE: StateCategory.AUTO_MAY_REST,
    CaseState.CALCULATING: StateCategory.AUTO,
    CaseState.INVESTIGATING: StateCategory.AUTO,
    CaseState.EXECUTING: StateCategory.AUTO,
    CaseState.VERIFYING: StateCategory.AUTO_MAY_REST,
    CaseState.AWAITING_SELLER: StateCategory.WAITING,
    CaseState.AWAITING_OPS: StateCategory.WAITING,
    CaseState.AWAITING_APPROVAL: StateCategory.WAITING,
    CaseState.NEEDS_HUMAN_REVIEW: StateCategory.HUMAN,
    CaseState.RESOLVED_CREDITED: StateCategory.TERMINAL,
    CaseState.RESOLVED_UPHELD: StateCategory.TERMINAL,
    CaseState.WITHDRAWN: StateCategory.TERMINAL,
    CaseState.CLOSED_NO_RESPONSE: StateCategory.TERMINAL,
    CaseState.REJECTED_INELIGIBLE: StateCategory.TERMINAL,
    CaseState.HANDED_TO_HUMAN: StateCategory.TERMINAL,
}


class ReopenRule(StrEnum):
    """Terminal states only."""

    HUMAN = "HUMAN"
    AGENT_WITHIN_WINDOW = "AGENT_WITHIN_WINDOW"
    NEVER = "NEVER"


class InboundEventType(StrEnum):
    DISPUTE_RAISED = "DISPUTE_RAISED"
    SELLER_REPLY_RECEIVED = "SELLER_REPLY_RECEIVED"
    DISPUTE_WITHDRAWN = "DISPUTE_WITHDRAWN"
    DELIVERY_FAILURE_RECEIVED = "DELIVERY_FAILURE_RECEIVED"
    OPS_FINDING_RECEIVED = "OPS_FINDING_RECEIVED"
    APPROVAL_DECIDED = "APPROVAL_DECIDED"
    TIMER_FIRED = "TIMER_FIRED"
    HUMAN_TOOK_CASE = "HUMAN_TOOK_CASE"
    HUMAN_RETURNED_CASE = "HUMAN_RETURNED_CASE"
    HUMAN_REOPENED_CASE = "HUMAN_REOPENED_CASE"
    HUMAN_CONFIRMED_WITHDRAWAL = "HUMAN_CONFIRMED_WITHDRAWAL"
    #: ADR 0050: a person decides the case from code's menu (uphold, credit, remeasure, ask).
    HUMAN_DECIDED_CASE = "HUMAN_DECIDED_CASE"


class InternalEventType(StrEnum):
    """Catalogue and payloads: events-catalog.md."""

    CASE_OPENED = "CASE_OPENED"
    STATE_CHANGED = "STATE_CHANGED"
    ELIGIBILITY_EVALUATED = "ELIGIBILITY_EVALUATED"
    EVIDENCE_RECORDED = "EVIDENCE_RECORDED"
    ASSESSMENT_RECORDED = "ASSESSMENT_RECORDED"
    INPUT_SCREENED = "INPUT_SCREENED"
    PATTERN_CONTEXT_LOADED = "PATTERN_CONTEXT_LOADED"
    PROPOSAL_RECORDED = "PROPOSAL_RECORDED"
    REASONER_FALLBACK = "REASONER_FALLBACK"
    INTENT_CREATED = "INTENT_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    APPROVAL_SUPERSEDED = "APPROVAL_SUPERSEDED"
    ACTION_PLANNED = "ACTION_PLANNED"
    ACTION_SENT = "ACTION_SENT"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_UNKNOWN = "ACTION_UNKNOWN"
    VERIFICATION_PENDING = "VERIFICATION_PENDING"
    ACTION_VERIFIED = "ACTION_VERIFIED"
    UNKNOWN_ACTION_RESOLVED = "UNKNOWN_ACTION_RESOLVED"
    TIMER_SCHEDULED = "TIMER_SCHEDULED"
    TIMER_CANCELLED = "TIMER_CANCELLED"
    EVENT_DEFERRED = "EVENT_DEFERRED"
    EVENT_IGNORED = "EVENT_IGNORED"
    REMINDER_SKIPPED = "REMINDER_SKIPPED"
    NOTICE_NOT_SENT = "NOTICE_NOT_SENT"
    #: ADR 0050: a person's decision that policy or code refused; the case stays with them.
    HUMAN_DECISION_REFUSED = "HUMAN_DECISION_REFUSED"
    CASE_OUTCOME_RECORDED = "CASE_OUTCOME_RECORDED"
    MODEL_ROUTE_FAILOVER = "MODEL_ROUTE_FAILOVER"


EventType = InboundEventType | InternalEventType


class TimerKind(StrEnum):
    SELLER_REMINDER = "SELLER_REMINDER"
    SELLER_DECISION = "SELLER_DECISION"
    OPS_TIMEOUT = "OPS_TIMEOUT"
    APPROVAL_EXPIRY = "APPROVAL_EXPIRY"
    VERIFY_RETRY = "VERIFY_RETRY"
    EVIDENCE_RETRY = "EVIDENCE_RETRY"


class Trigger(StrEnum):
    """Exactly the `triggers` keys of transitions.yaml. Card P1-03 asserts that at test time."""

    CASE_OPENED = "CASE_OPENED"
    ELIGIBILITY_PASSED = "ELIGIBILITY_PASSED"
    ELIGIBILITY_FAILED = "ELIGIBILITY_FAILED"
    EVIDENCE_RECORDED = "EVIDENCE_RECORDED"
    ASSESSMENT_READY = "ASSESSMENT_READY"
    ASSESSMENT_INELIGIBLE = "ASSESSMENT_INELIGIBLE"
    VERDICT_ALLOW = "VERDICT_ALLOW"
    VERDICT_REQUIRE_APPROVAL = "VERDICT_REQUIRE_APPROVAL"
    PROPOSAL_WAIT = "PROPOSAL_WAIT"
    REMINDER_ALLOW = "REMINDER_ALLOW"
    REMINDER_REQUIRE_APPROVAL = "REMINDER_REQUIRE_APPROVAL"
    FOLLOW_UP_ALLOW = "FOLLOW_UP_ALLOW"
    FOLLOW_UP_REQUIRE_APPROVAL = "FOLLOW_UP_REQUIRE_APPROVAL"
    FOLLOW_UP_BLOCKED = "FOLLOW_UP_BLOCKED"
    ACTION_SENT = "ACTION_SENT"
    ACTION_VERIFIED = "ACTION_VERIFIED"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"
    SELLER_REPLY_RECEIVED = "SELLER_REPLY_RECEIVED"
    OPS_FINDING_RECEIVED = "OPS_FINDING_RECEIVED"
    DISPUTE_WITHDRAWN = "DISPUTE_WITHDRAWN"
    DISPUTE_RAISED_AGAIN = "DISPUTE_RAISED_AGAIN"
    SELLER_DECISION_DUE = "SELLER_DECISION_DUE"
    HUMAN_TOOK_CASE = "HUMAN_TOOK_CASE"
    HUMAN_RETURNED_CASE = "HUMAN_RETURNED_CASE"
    HUMAN_REOPENED_CASE = "HUMAN_REOPENED_CASE"
    HUMAN_CONFIRMED_WITHDRAWAL = "HUMAN_CONFIRMED_WITHDRAWAL"
    LATE_INBOUND = "LATE_INBOUND"
    ESCALATE = "ESCALATE"


class ProposalAction(StrEnum):
    """Mirrors prompts/case_investigator/v1/schema.json."""

    PROPOSE_CREDIT = "propose_credit"
    UPHOLD_CHARGE = "uphold_charge"
    REQUEST_SELLER_EVIDENCE = "request_seller_evidence"
    REQUEST_REMEASUREMENT = "request_remeasurement"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    WAIT = "wait"


class CautionRank(IntEnum):
    """Total order for the Phase 7 anchoring control (ADR 0025, kernel-interfaces section 15.3).

    Higher is more cautious. propose_credit ranks lowest because it is the only action that
    moves money; uphold_charge sits just above it because it closes the case against the
    seller. The order must stay total: a tie is how a pattern turns an uphold into a credit,
    which I7 forbids.
    """

    PROPOSE_CREDIT = 0
    UPHOLD_CHARGE = 1
    WAIT = 2
    REQUEST_SELLER_EVIDENCE = 3
    REQUEST_REMEASUREMENT = 4
    ESCALATE_TO_HUMAN = 5


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingKind(StrEnum):
    VERIFIED_FACT = "verified_fact"
    PLAUSIBLE_EXPLANATION = "plausible_explanation"
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


class ActionType(StrEnum):
    SEND_MESSAGE = "SEND_MESSAGE"
    UPSERT_ISSUE = "UPSERT_ISSUE"
    POST_CREDIT = "POST_CREDIT"


class MessageKind(StrEnum):
    EVIDENCE_REQUEST = "evidence_request"
    REMINDER = "reminder"
    HANDED_TO_HUMAN_NOTICE = "handed_to_human_notice"
    UPHELD_EXPLANATION = "upheld_explanation"
    CREDIT_CONFIRMATION = "credit_confirmation"
    CLARIFICATION_REQUEST = "clarification_request"


class VerifiedKind(StrEnum):
    """The verified_kind carried by Trigger.ACTION_VERIFIED."""

    EVIDENCE_REQUEST = "EVIDENCE_REQUEST"
    REMINDER = "REMINDER"
    REMEASUREMENT_ISSUE = "REMEASUREMENT_ISSUE"
    UPHELD_EXPLANATION = "UPHELD_EXPLANATION"
    CREDIT = "CREDIT"
    CREDIT_CONFIRMATION = "CREDIT_CONFIRMATION"
    HANDED_TO_HUMAN_NOTICE = "HANDED_TO_HUMAN_NOTICE"
    CLARIFICATION_REQUEST = "CLARIFICATION_REQUEST"


class IntentSource(StrEnum):
    PROPOSAL = "PROPOSAL"
    TIMER = "TIMER"
    FOLLOW_UP = "FOLLOW_UP"


class IntentStatus(StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PLANNED = "PLANNED"
    SUPERSEDED = "SUPERSEDED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ActionStatus(StrEnum):
    PLANNED = "PLANNED"
    SENT = "SENT"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class ApprovalSubject(StrEnum):
    CREDIT = "CREDIT"
    MESSAGE = "MESSAGE"


class ApprovalDecision(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"


class EvidenceStatus(StrEnum):
    FOUND = "FOUND"
    MISSING = "MISSING"
    FAILED = "FAILED"


class Provenance(StrEnum):
    SYSTEM = "SYSTEM"
    SELLER = "SELLER"
    OPS = "OPS"
    AI_DERIVED = "AI_DERIVED"


class VerdictKind(IntEnum):
    """Ordered by caution; combining verdicts takes the max (kernel-interfaces section 7).

    A caution signal may only raise this value. There is deliberately no operation that
    lowers it (I7, I15).
    """

    ALLOW = 0
    REQUIRE_APPROVAL = 1
    ESCALATE = 2
    DENY = 3


class PolicyStage(StrEnum):
    PLANNING = "PLANNING"
    EXECUTION = "EXECUTION"


class InboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    DEFERRED = "DEFERRED"
    UNROUTABLE = "UNROUTABLE"
    FAILED = "FAILED"


class Reaction(StrEnum):
    TRANSITION = "TRANSITION"
    STEP = "STEP"
    ABSORB = "ABSORB"
    DEFER = "DEFER"
    IGNORE = "IGNORE"


class CauseKind(StrEnum):
    INPUT_EVENT = "INPUT_EVENT"
    STEP = "STEP"
    TIMER = "TIMER"
    HUMAN = "HUMAN"
    RECOVERY = "RECOVERY"


class ActorKind(StrEnum):
    SYSTEM = "SYSTEM"
    HUMAN = "HUMAN"
    EXTERNAL = "EXTERNAL"


class ScreenKind(StrEnum):
    SELLER_REPLY = "SELLER_REPLY"
    OPS_NOTE = "OPS_NOTE"
    DISPUTE_STATEMENT = "DISPUTE_STATEMENT"
    SHIPMENT_NOTE = "SHIPMENT_NOTE"


class ReviewCode(StrEnum):
    """Why a case is with a person. MODULE_UNAVAILABLE renders as "module_unavailable:<module>"."""

    MODULE_UNAVAILABLE = "module_unavailable"
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
    CREDIT_REJECTED = "credit_rejected"
    OPS_TIMEOUT = "ops_timeout"
    DELIVERY_FAILED = "delivery_failed"
    LATE_INBOUND = "late_inbound"
    STEP_LIMIT = "step_limit"
    REPLY_UNRESOLVED = "reply_unresolved"
    INTERNAL_ERROR = "internal_error"

    # Added by the Stage A2 amendment (kernel-interfaces.md section 13, second block).
    # policy-rules.md section 8 maps each rule reason to the code of the same name:
    # R21 -> MANIPULATION_SUSPECTED, R22 -> LOW_CONFIDENCE, R14 -> MULTIPLE_DEFINITE_ERRORS,
    # R16 -> LEDGER_STATE_UNKNOWN, the DMARC signal -> SENDER_UNAUTHENTICATED.
    # CREDIT_WINDOW_ENDED pairs with the TransientPortError reason of the same name (F01).
    MANIPULATION_SUSPECTED = "manipulation_suspected"
    LOW_CONFIDENCE = "low_confidence"
    MULTIPLE_DEFINITE_ERRORS = "multiple_definite_errors"
    LEDGER_STATE_UNKNOWN = "ledger_state_unknown"
    SENDER_UNAUTHENTICATED = "sender_unauthenticated"
    CREDIT_WINDOW_ENDED = "credit_window_ended"
