"""Audit AUD-12: what a later round of a case remembers of the earlier ones (ADR 0072).

Structured only: each earlier round's last machine proposal (action, confidence, kinds of
finding, what it cited) and why a person rejected a proposal. Never model-written text.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from dispute_agent.kernel.domain.enums import (
    ApprovalDecision,
    Confidence,
    FindingKind,
    ProposalAction,
)
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep


def proposal(round_: int, action: ProposalAction, refs: tuple[str, ...], by: str = "llm") -> Any:
    finding = SimpleNamespace(kind=FindingKind.VERIFIED_FACT, evidence_refs=refs)
    return SimpleNamespace(
        reasoner=by,
        gather_round=round_,
        action=action,
        confidence=Confidence.MEDIUM,
        findings=(finding, finding),
        rationale="model-written text that must not carry over",
    )


def station(proposals: list[Any], approvals: list[Any]) -> Any:
    return SimpleNamespace(
        proposals=SimpleNamespace(for_case=lambda _id: proposals),
        approvals=SimpleNamespace(for_case=lambda _id: approvals),
    )


CASE = SimpleNamespace(id="case_1", gather_round=3)


def test_each_earlier_round_s_last_machine_proposal_structured_only() -> None:
    held = station(
        [
            proposal(1, ProposalAction.REQUEST_SELLER_EVIDENCE, ("T1.1",)),
            proposal(1, ProposalAction.REQUEST_REMEASUREMENT, ("T1.1", "ev-1")),  # round 1's last
            proposal(2, ProposalAction.UPHOLD_CHARGE, (), by="human"),  # a person's: not memory
            proposal(3, ProposalAction.ESCALATE_TO_HUMAN, ()),  # this round: not "earlier"
        ],
        [],
    )
    rounds = InvestigateStep._prior_rounds(held, CASE)  # type: ignore[arg-type]
    assert [(r.gather_round, r.action) for r in rounds] == [
        (1, ProposalAction.REQUEST_REMEASUREMENT)
    ]
    assert rounds[0].evidence_refs == ("T1.1", "ev-1")  # each once, in order
    assert "rationale" not in rounds[0].model_dump()


def test_a_reviewer_s_rejection_notes_newest_first_and_at_most_three() -> None:
    def approval(decision: ApprovalDecision, note: str) -> Any:
        return SimpleNamespace(decision=decision, note=note)

    held = station(
        [],
        [
            approval(ApprovalDecision.REJECTED, "oldest"),
            approval(ApprovalDecision.APPROVED, "approved: not a lesson"),
            approval(ApprovalDecision.REJECTED, "  "),  # no reason given
            *(approval(ApprovalDecision.REJECTED, f"note {n}") for n in range(3)),
        ],
    )
    notes = InvestigateStep._reviewer_notes(held, CASE)  # type: ignore[arg-type]
    assert notes == ("note 2", "note 1", "note 0")
