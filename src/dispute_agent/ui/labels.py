"""Plain-language names for what the system records (ADR 0044).

One place, so every screen says the same thing for the same state. A label never changes
what it names: a case that is "With a person" is in NEEDS_HUMAN_REVIEW, nothing softer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import CaseState, MessageKind, ProposalAction, ReviewCode

if TYPE_CHECKING:
    from decimal import Decimal


class Tone(StrEnum):
    """How a status reads at a glance. `settled` is the one place the accent is spent."""

    SETTLED = "settled"
    DONE = "done"
    WORKING = "working"
    WAITING = "waiting"
    ATTENTION = "attention"
    CLOSED = "closed"


STATE: dict[CaseState, tuple[str, Tone]] = {
    CaseState.RECEIVED: ("Received", Tone.WORKING),
    CaseState.GATHERING_EVIDENCE: ("Gathering evidence", Tone.WORKING),
    CaseState.CALCULATING: ("Recalculating", Tone.WORKING),
    CaseState.INVESTIGATING: ("Investigating", Tone.WORKING),
    CaseState.NEEDS_HUMAN_REVIEW: ("With a person", Tone.ATTENTION),
    CaseState.AWAITING_SELLER: ("Waiting for the seller", Tone.WAITING),
    CaseState.AWAITING_OPS: ("Waiting for the hub", Tone.WAITING),
    CaseState.AWAITING_APPROVAL: ("Awaiting approval", Tone.WAITING),
    CaseState.EXECUTING: ("Acting", Tone.WORKING),
    CaseState.VERIFYING: ("Checking it happened", Tone.WORKING),
    CaseState.RESOLVED_CREDITED: ("Resolved: credited", Tone.SETTLED),
    CaseState.RESOLVED_UPHELD: ("Resolved: charge upheld", Tone.DONE),
    CaseState.WITHDRAWN: ("Withdrawn", Tone.CLOSED),
    CaseState.CLOSED_NO_RESPONSE: ("Closed: no reply", Tone.CLOSED),
    CaseState.REJECTED_INELIGIBLE: ("Not eligible", Tone.CLOSED),
    CaseState.HANDED_TO_HUMAN: ("Handed to a person", Tone.ATTENTION),
}

ACTION: dict[ProposalAction, str] = {
    ProposalAction.PROPOSE_CREDIT: "Credit the seller",
    ProposalAction.UPHOLD_CHARGE: "Uphold the charge",
    ProposalAction.REQUEST_SELLER_EVIDENCE: "Ask the seller for evidence",
    ProposalAction.REQUEST_REMEASUREMENT: "Ask the hub to re-measure",
    ProposalAction.ESCALATE_TO_HUMAN: "Hand to a person",
    ProposalAction.WAIT: "Wait",
}

#: Evidence requirement keys, as a reviewer would name them. Unknown keys show as recorded.
EVIDENCE: dict[str, str] = {
    "shipment.status": "Delivery status",
    "shipment.declared_dims": "Declared size and weight",
    "shipment.measured_dims": "Hub measurement",
    "shipment.zone": "Delivery zone",
    "ledger.entries": "Billing entries",
    "shipment.image_description": "Parcel image",
    "shipment.history": "Earlier shipments of this item",
    "ops.finding": "Hub finding",
    "seller_reply": "Seller's reply",
}

FINDING: dict[str, str] = {
    "verified_fact": "Verified fact",
    "plausible_explanation": "Possible explanation",
    "missing_evidence": "Missing evidence",
    "conflicting_evidence": "Conflicting evidence",
}


#: Which email an action or approval is about, so a step says which message (HANDOFF 4.2).
MESSAGE: dict[MessageKind, str] = {
    MessageKind.CREDIT_CONFIRMATION: "the credit confirmation email",
    MessageKind.EVIDENCE_REQUEST: "the evidence request email",
    MessageKind.UPHELD_EXPLANATION: "the explanation email",
    MessageKind.REMINDER: "the reminder email",
    MessageKind.HANDED_TO_HUMAN_NOTICE: "the handed-to-a-person email",
    MessageKind.CLARIFICATION_REQUEST: "the clarification email",
}

#: The rules engine's rows (packs/weight_dispute/baseline.py) in plain words. The code itself
#: goes in Details; a reader should not need to know what "B5" means.
RULE: dict[str, str] = {
    "B1": "Some evidence the agent needs was not available, so a person decides.",
    "B2": "The seller's text read like instructions to the agent. It was treated as data, "
    "not followed.",
    "B3": "Code proved one definite error, so the credit amount is code's own figure.",
    "B4": "Code proved more than one error, so there is no single amount to credit.",
    "B5": "The round contains a parcel photo, and images need a person's judgement.",
    "B6": "A re-measure the agent asked for is still outstanding.",
    "B7": "The hub's measured size is more than 5% larger than declared, so the seller is "
    "asked for evidence.",
    "B8": "The charge follows the rate card and the parcel measured within 5% of declared.",
    "B9": "No rule settles this case, so a person decides.",
}

#: Why a case is with a person, in words (brief 9.3). The code goes in Details.
REASON: dict[ReviewCode, str] = {
    ReviewCode.PROPOSAL_ESCALATED: "Proposal escalated",
    ReviewCode.POLICY_DENIED: "Policy denied",
    ReviewCode.POLICY_ESCALATED: "Policy escalated",
    ReviewCode.VERIFICATION_FAILED: "Could not prove it happened",
    ReviewCode.OPS_TIMEOUT: "The hub did not answer in time",
    ReviewCode.APPROVAL_EXPIRED: "No one approved it within 72 hours",
    ReviewCode.GUARDRAIL_BLOCKED: "The input screen flagged it",
    ReviewCode.MODULE_UNAVAILABLE: "A module the agent needs is switched off",
    ReviewCode.KILL_SWITCH_ACTIVE: "Outbound actions are paused",
    ReviewCode.ELIGIBILITY_UNKNOWN: "The shipment's status could not be read",
    ReviewCode.SELLER_WITHDRAWAL_CLAIMED: "The seller wrote that they withdraw",
    ReviewCode.LLM_BUDGET_EXCEEDED: "The AI spend cap was reached",
    ReviewCode.CREDIT_CAP_REACHED: "The credit cap was reached",
    ReviewCode.APPROVAL_REJECTED_LIMIT: "A reviewer rejected it twice",
    ReviewCode.CREDIT_REJECTED: "A reviewer rejected the credit",
    ReviewCode.ACTION_FAILED: "An action failed",
    ReviewCode.ACTION_UNKNOWN: "An action's outcome is unknown",
    ReviewCode.DELIVERY_FAILED: "An email could not be delivered",
}


REASON.update(
    {
        ReviewCode.MANIPULATION_SUSPECTED: "The input screen flagged it",
        ReviewCode.LOW_CONFIDENCE: "The proposal's confidence was too low",
        ReviewCode.MULTIPLE_DEFINITE_ERRORS: "Code proved more than one error",
        ReviewCode.LEDGER_STATE_UNKNOWN: "The ledger's state could not be read",
        ReviewCode.SENDER_UNAUTHENTICATED: "A reply's sender could not be verified",
        ReviewCode.CREDIT_WINDOW_ENDED: "The window for credits has ended",
    }
)

#: What a rule code in a review detail means, when it is not one of the rules engine's rows.
RULE_DETAIL = {
    "R21": "the seller's text read like instructions to the agent. It was treated as data, "
    "not followed.",
}


def handed_over(code: ReviewCode, detail: str) -> tuple[str, str]:
    """Why a case is with a person, as one sentence and the rule code behind it.

    "B5: the round contains image evidence ..." becomes ("Proposal escalated: the round
    contains a parcel photo, and images need a person's judgement.", "B5"). A detail with no
    known code is kept as recorded after the reason's words.
    """
    head = reason(code)
    rule = detail.partition(":")[0].strip()
    meaning = RULE.get(rule) or RULE_DETAIL.get(rule)
    if rule == "B2":  # the screen flagged it: say so in the screen's own terms
        head, meaning = REASON[ReviewCode.MANIPULATION_SUSPECTED], RULE_DETAIL["R21"]
    if meaning:
        return f"{head}: {meaning[0].lower()}{meaning[1:]}", rule
    return (f"{head}. {detail}" if detail else f"{head}."), ""


def state(value: CaseState) -> tuple[str, Tone]:
    return STATE[value]


def reason(code: ReviewCode) -> str:
    return REASON.get(code, words(code.value).capitalize())


def rule_words(rationale: str) -> tuple[str, str]:
    """A rules-engine rationale ("B5: the round contains ...") as (words, code). The words are
    the plain sentence for the row; anything else is returned as recorded, with no code."""
    code, _, rest = rationale.partition(":")
    code = code.strip()
    if code in RULE and rest:
        return RULE[code], code
    return rationale, ""


def money(amount: Decimal) -> str:
    """₹1,820.00: every amount on every screen, one way."""
    return f"₹{amount:,.2f}"


def clock(moment: datetime) -> str:
    """The time alone ("16:08"), for steps inside one case's timeline."""
    utc = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    return f"{utc:%H:%M}"


def when(moment: datetime) -> str:
    """Every time on every screen, one way: "7 Nov 2026, 16:08 UTC" (one zone, no seconds)."""
    utc = moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    return f"{utc.day} {utc:%b %Y, %H:%M} UTC"


def words(code: str) -> str:
    """A recorded code as words: `remeasurement_lower` becomes `remeasurement lower`."""
    return code.replace("_", " ").replace(":", ": ").strip()


__all__ = [
    "ACTION",
    "EVIDENCE",
    "FINDING",
    "MESSAGE",
    "REASON",
    "RULE",
    "STATE",
    "Tone",
    "clock",
    "handed_over",
    "money",
    "reason",
    "rule_words",
    "state",
    "when",
    "words",
]
