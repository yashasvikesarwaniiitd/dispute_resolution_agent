"""P7-06: two-pass reasoning connected - the budget, and code deciding "the pattern changed it"."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dispute_agent.infra.policy import load_policy, pattern_call_limit, with_pattern_budget
from dispute_agent.kernel.domain.enums import ProposalAction
from dispute_agent.kernel.policy.signals import pattern_changed_signal
from dispute_agent.modules.reasoning.llm.two_pass import BLOCKED, TwoPassReasoner
from tests.unit.modules.reasoning.test_two_pass import chooser, proposal

CONFIG = Path(__file__).resolve().parents[4] / "config"
A = ProposalAction


class TestTheBudget:
    def test_the_limit_is_ten_with_cross_case_on(self) -> None:
        policy = with_pattern_budget(
            load_policy(CONFIG), cross_case_on=True, limit=pattern_call_limit(CONFIG)
        )
        assert policy.llm.max_calls_per_case == 10

    def test_the_limit_stays_six_with_cross_case_off(self) -> None:
        policy = with_pattern_budget(
            load_policy(CONFIG), cross_case_on=False, limit=pattern_call_limit(CONFIG)
        )
        assert policy.llm.max_calls_per_case == 6

    def test_a_higher_limit_is_never_lowered(self) -> None:
        base = load_policy(CONFIG)
        high = base.model_copy(
            update={"llm": base.llm.model_copy(update={"max_calls_per_case": 20})}
        )
        assert with_pattern_budget(high, cross_case_on=True, limit=10).llm.max_calls_per_case == 20

    def test_a_patterned_case_completes_within_the_raised_limit(self) -> None:
        """Two rounds with patterns cost four investigator calls - plus a retry each, eight.
        Inside 10; over the old 6, which would have escalated llm_budget_exceeded."""
        calls: list[Any] = []

        class Inner:
            def propose(self, briefing: Any) -> Any:
                calls.append(briefing)
                return proposal(A.REQUEST_SELLER_EVIDENCE)

        two = TwoPassReasoner(inner=Inner())

        class Briefing:
            pattern_context = ("p",)

            def model_copy(self, update: dict[str, Any]) -> Briefing:
                return self

        for _ in range(2):
            two.propose(Briefing())  # type: ignore[arg-type]
        with_retries = len(calls) * 2
        assert len(calls) == 4
        assert with_retries <= 10 < with_retries + 3
        assert with_retries > 6


class TestPatternChanged:
    def test_a_more_cautious_second_pass_is_marked_changed(self) -> None:
        result = chooser().choose(
            proposal(A.REQUEST_SELLER_EVIDENCE), proposal(A.ESCALATE_TO_HUMAN)
        )
        assert result.pattern_influence.changed_recommendation is True
        assert result.pattern_influence.note.startswith("pattern_changed")

    def test_the_same_action_is_not_a_change_whatever_the_model_claims(self) -> None:
        # The factory's proposals claim changed_recommendation=True; code decides.
        result = chooser().choose(
            proposal(A.REQUEST_SELLER_EVIDENCE), proposal(A.REQUEST_SELLER_EVIDENCE)
        )
        assert result.pattern_influence.changed_recommendation is False

    def test_a_blocked_relaxation_is_not_a_change(self) -> None:
        result = chooser().choose(proposal(A.UPHOLD_CHARGE), proposal(A.PROPOSE_CREDIT))
        assert result.action is A.UPHOLD_CHARGE
        assert result.pattern_influence.note.startswith(BLOCKED)
        assert result.pattern_influence.changed_recommendation is False

    def test_the_change_becomes_an_approval_floor(self) -> None:
        signal = pattern_changed_signal("case_01HQ")
        assert signal.kind == "pattern_changed_recommendation"
        assert signal.min_verdict.name == "REQUIRE_APPROVAL"
