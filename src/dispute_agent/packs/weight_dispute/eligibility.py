"""May this dispute proceed at all (P2-01, policy-rules.md)?

A weight dispute is about what a parcel was charged for carrying, so there has to have been
a carriage. A shipment that came back to the seller was never delivered, and there is
nothing to dispute.

**"Unknown" is a real answer here, and the important one.** When the facts do not say what
happened to the parcel, this returns `unknown` and the case goes to a person
(`eligibility_unknown`). It does not guess "probably delivered" — crediting a parcel that
never moved — and it does not guess "probably not" either, which is worse: a dispute refused
on a shipment status nobody could confirm is a dispute refused for the wrong reason, and the
seller has no way to tell that from a refusal on the merits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.evidence import EligibilityResult

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.case import CaseFacts

#: The pack_facts key intake fills from the shipment record.
STATUS_KEY = "shipment_status"

#: The one status a weight dispute can be raised against.
DELIVERED = "DELIVERED"

#: Statuses that mean the parcel did not complete its journey. Anything else that is not
#: DELIVERED is unknown rather than ineligible — a status this pack has not been taught is
#: not evidence that nothing was carried.
NOT_DELIVERED = frozenset({"RTO", "RETURNED", "CANCELLED", "LOST"})

NOT_DELIVERED_CODE = "not_delivered"
STATUS_UNKNOWN_CODE = "shipment_status_unknown"


def eligibility(facts: CaseFacts) -> EligibilityResult:
    """Pure. Three outcomes, and `unknown` is not a failure mode."""
    status = facts.pack_facts.get(STATUS_KEY)

    if status == DELIVERED:
        return EligibilityResult(outcome="eligible")
    if isinstance(status, str) and status.upper() in NOT_DELIVERED:
        return EligibilityResult(outcome="ineligible", reason_codes=(NOT_DELIVERED_CODE,))
    return EligibilityResult(outcome="unknown", reason_codes=(STATUS_UNKNOWN_CODE,))


__all__ = [
    "DELIVERED",
    "NOT_DELIVERED",
    "NOT_DELIVERED_CODE",
    "STATUS_KEY",
    "STATUS_UNKNOWN_CODE",
    "eligibility",
]
