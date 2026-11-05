"""The LangGraph investigator (ADR 0041): two passes, reconciled by code, only ever more careful."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dispute_agent.kernel.domain.caution_rank import rank
from dispute_agent.kernel.domain.enums import Confidence, ProposalAction
from dispute_agent.kernel.domain.errors import InvalidModelOutput, TransientPortError
from dispute_agent.modules.reasoning.llm.graph import GraphReasoner
from tests.unit.modules.reasoning.test_two_pass import proposal

A = ProposalAction
C = Confidence


@dataclass
class Scripted:
    """An inner reasoner that answers from a list, and counts its calls."""

    answers: list[Any]
    calls: int = 0
    seen: list[Any] = field(default_factory=list)

    def propose(self, briefing: Any) -> Any:
        self.calls += 1
        self.seen.append(briefing)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(*answers: Any) -> tuple[Any, Scripted]:
    inner = Scripted(list(answers))
    return GraphReasoner(inner).propose("briefing"), inner  # type: ignore[arg-type]


def test_two_independent_passes_on_the_same_briefing() -> None:
    _, inner = run(proposal(A.REQUEST_SELLER_EVIDENCE), proposal(A.REQUEST_SELLER_EVIDENCE))
    assert inner.calls == 2 and inner.seen == ["briefing", "briefing"]


def test_agreement_keeps_the_action_at_the_lower_confidence() -> None:
    result, _ = run(proposal(A.PROPOSE_CREDIT, C.HIGH), proposal(A.PROPOSE_CREDIT, C.MEDIUM))
    assert (result.action, result.confidence) == (A.PROPOSE_CREDIT, C.MEDIUM)


def test_disagreement_keeps_the_more_cautious_action_at_low_confidence() -> None:
    result, _ = run(proposal(A.PROPOSE_CREDIT, C.HIGH), proposal(A.ESCALATE_TO_HUMAN, C.HIGH))
    assert (result.action, result.confidence) == (A.ESCALATE_TO_HUMAN, C.LOW)


@pytest.mark.parametrize("first", list(A))
@pytest.mark.parametrize("second", list(A))
def test_the_result_is_never_less_cautious_than_either_pass(
    first: ProposalAction, second: ProposalAction
) -> None:
    result, _ = run(proposal(first), proposal(second))
    assert rank(result.action) == max(rank(first), rank(second))


def test_a_failed_second_pass_keeps_the_first_at_low_confidence() -> None:
    result, _ = run(proposal(A.UPHOLD_CHARGE, C.HIGH), InvalidModelOutput("bad json"))
    assert (result.action, result.confidence) == (A.UPHOLD_CHARGE, C.LOW)


def test_a_failed_first_pass_fails_as_before() -> None:
    with pytest.raises(InvalidModelOutput):
        run(InvalidModelOutput("bad json"))


def test_an_unreachable_model_propagates_so_the_rules_baseline_can_take_over() -> None:
    with pytest.raises(TransientPortError):
        run(TransientPortError("down"))
