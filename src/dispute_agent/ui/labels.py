"""Plain-language names for what the system records (ADR 0044).

One place, so every screen says the same thing for the same state. A label never changes
what it names: a case that is "With a person" is in NEEDS_HUMAN_REVIEW, nothing softer.
"""

from __future__ import annotations

from enum import StrEnum

from dispute_agent.kernel.domain.enums import CaseState, ProposalAction


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


def state(value: CaseState) -> tuple[str, Tone]:
    return STATE[value]


def words(code: str) -> str:
    """A recorded code as words: `remeasurement_lower` becomes `remeasurement lower`."""
    return code.replace("_", " ").replace(":", ": ").strip()


__all__ = ["ACTION", "EVIDENCE", "FINDING", "STATE", "Tone", "state", "words"]
