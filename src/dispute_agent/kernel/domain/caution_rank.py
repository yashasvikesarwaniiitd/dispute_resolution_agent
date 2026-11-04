"""The total caution order over proposal actions (ADR 0025, agent-design.md section 7, I7).

Strictest last. `propose_credit` is the least cautious because it is the only action that
moves money; `uphold_charge` sits just above it because it closes the case against the
seller. Total by construction - every action has exactly one rank - so "is P2 at least as
cautious as P1?" always has an answer, which is the property the anchoring control needs.
"""

from __future__ import annotations

from enum import IntEnum

from dispute_agent.kernel.domain.enums import Confidence, ProposalAction


class CautionRank(IntEnum):
    PROPOSE_CREDIT = 0
    UPHOLD_CHARGE = 1
    WAIT = 2
    REQUEST_SELLER_EVIDENCE = 3
    REQUEST_REMEASUREMENT = 4
    ESCALATE_TO_HUMAN = 5


def rank(action: ProposalAction) -> CautionRank:
    return CautionRank[action.name]


CONFIDENCE_ORDER = {Confidence.LOW: 0, Confidence.MEDIUM: 1, Confidence.HIGH: 2}


__all__ = ["CONFIDENCE_ORDER", "CautionRank", "rank"]
