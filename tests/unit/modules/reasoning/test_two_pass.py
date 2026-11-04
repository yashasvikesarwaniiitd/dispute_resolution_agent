"""Two-pass anchoring control (P4-08, ADR 0025, I7): a pattern may add caution, never remove it."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import permutations
from typing import Any

import pytest

from dispute_agent.kernel.domain.caution_rank import CautionRank, rank
from dispute_agent.kernel.domain.enums import Confidence, FindingKind, ProposalAction
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import CaseId, ProposalId
from dispute_agent.kernel.domain.reasoning import (
    ActionParameters,
    Finding,
    PatternInfluence,
    Proposal,
)
from dispute_agent.modules.reasoning.llm.two_pass import BLOCKED, TwoPassReasoner

NOW = datetime(2026, 11, 4, tzinfo=UTC)


def proposal(
    action: ProposalAction,
    confidence: Confidence = Confidence.MEDIUM,
    params: ActionParameters | None = None,
) -> Proposal:
    return Proposal(
        id=ProposalId("prp_1"),
        case_id=CaseId("case_01HQ"),
        gather_round=1,
        action=action,
        confidence=confidence,
        rationale="r",
        findings=(Finding(kind=FindingKind.VERIFIED_FACT, statement="s", evidence_refs=()),),
        action_parameters=params or ActionParameters(),
        pattern_influence=PatternInfluence(considered=True, changed_recommendation=True),
        reasoner="rules_only",
        briefing_hash=payload_hash("b"),
        created_at=NOW,
    )


def chooser() -> TwoPassReasoner:
    return TwoPassReasoner(inner=None)  # type: ignore[arg-type]


class TestTheOrderIsTotal:
    def test_every_action_has_a_rank(self) -> None:
        assert {rank(a) for a in ProposalAction} == set(CautionRank)

    def test_every_pair_compares(self) -> None:
        for a, b in permutations(ProposalAction, 2):
            assert (rank(a) < rank(b)) or (rank(a) > rank(b))

    def test_credit_is_least_cautious_and_escalation_most(self) -> None:
        assert min(ProposalAction, key=rank) is ProposalAction.PROPOSE_CREDIT
        assert max(ProposalAction, key=rank) is ProposalAction.ESCALATE_TO_HUMAN


class TestRelaxationIsBlocked:
    def test_uphold_then_credit_returns_the_uphold(self) -> None:
        """The exact hole the Stage A4 review found."""
        p1 = proposal(ProposalAction.UPHOLD_CHARGE)
        p2 = proposal(ProposalAction.PROPOSE_CREDIT)
        two = chooser()
        result = two.choose(p1, p2)
        assert result.action is ProposalAction.UPHOLD_CHARGE
        assert result.pattern_influence.note.startswith(BLOCKED)
        assert two.blocked

    @pytest.mark.parametrize(("first", "second"), list(permutations(ProposalAction, 2)))
    def test_the_result_is_never_less_cautious_than_pass_one(
        self, first: ProposalAction, second: ProposalAction
    ) -> None:
        result = chooser().choose(proposal(first), proposal(second))
        assert rank(result.action) >= rank(first)

    def test_a_more_cautious_second_pass_is_accepted(self) -> None:
        p1 = proposal(ProposalAction.UPHOLD_CHARGE)
        p2 = proposal(ProposalAction.ESCALATE_TO_HUMAN)
        result = chooser().choose(p1, p2)
        assert result.action is ProposalAction.ESCALATE_TO_HUMAN
        assert not result.pattern_influence.note.startswith(BLOCKED)


class TestConfidenceAndParametersAreNotRaised:
    def test_raising_confidence_keeps_pass_one_s_so_r22_still_fires(self) -> None:
        p1 = proposal(ProposalAction.UPHOLD_CHARGE, Confidence.LOW)
        p2 = proposal(ProposalAction.UPHOLD_CHARGE, Confidence.MEDIUM)
        result = chooser().choose(p1, p2)
        assert result.confidence is Confidence.LOW
        assert result.pattern_influence.note.startswith(BLOCKED)

    def test_changed_parameters_keep_pass_one_s(self) -> None:
        p1 = proposal(
            ProposalAction.REQUEST_SELLER_EVIDENCE,
            params=ActionParameters(requested_items=("photo",)),
        )
        p2 = proposal(
            ProposalAction.REQUEST_SELLER_EVIDENCE,
            params=ActionParameters(requested_items=("nothing needed",)),
        )
        result = chooser().choose(p1, p2)
        assert result.action_parameters.requested_items == ("photo",)

    def test_lowering_confidence_is_allowed(self) -> None:
        p1 = proposal(ProposalAction.UPHOLD_CHARGE, Confidence.HIGH)
        p2 = proposal(ProposalAction.UPHOLD_CHARGE, Confidence.LOW)
        assert chooser().choose(p1, p2).confidence is Confidence.LOW


class TestWithoutPatternsItIsOnePass:
    def test_no_patterns_means_the_inner_reasoner_is_asked_once(self) -> None:
        calls: list[Any] = []

        class Inner:
            def propose(self, briefing: Any) -> Proposal:
                calls.append(briefing)
                return proposal(ProposalAction.WAIT)

        class Briefing:
            pattern_context = ()

        TwoPassReasoner(inner=Inner()).propose(Briefing())  # type: ignore[arg-type]
        assert len(calls) == 1
