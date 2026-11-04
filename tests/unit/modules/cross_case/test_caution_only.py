"""P7-11 proof 3: pattern influence can only increase caution (I7), over random inputs.

Three places a pattern touches a decision, each checked for every input hypothesis draws:

1. **The verdict.** For any rule results and any set of pattern signals (matches with either
   floor, an unavailable store, a changed recommendation), the combined verdict with the
   signals is never lower than without them - and never ALLOW once any pattern is present.
2. **The action.** For any pair of passes, the two-pass result is never less cautious than the
   independent view (`CautionRank`, ADR 0025), and the code-decided `changed_recommendation`
   is true only when the action became more cautious.
3. **The signal itself.** No pattern signal can say ALLOW: the type cannot form it.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from dispute_agent.kernel.domain.actions import RuleResult
from dispute_agent.kernel.domain.caution_rank import rank
from dispute_agent.kernel.domain.enums import ProposalAction, VerdictKind
from dispute_agent.kernel.domain.patterns import CautionSignal
from dispute_agent.kernel.policy.signals import (
    pattern_changed_signal,
    pattern_context_unavailable_signal,
    pattern_match_signal,
)
from dispute_agent.kernel.policy.verdicts import verdicts_of
from tests.unit.modules.reasoning.test_two_pass import chooser, proposal

verdict_kinds = st.sampled_from(list(VerdictKind))
rule_results = st.lists(
    verdict_kinds.map(lambda v: RuleResult(rule_id="R99", verdict=v, reason="drawn")),
    max_size=6,
)
pattern_signals = st.lists(
    st.one_of(
        st.sampled_from([VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE]).map(
            lambda floor: pattern_match_signal("pat_x", floor)
        ),
        st.just(pattern_context_unavailable_signal()),
        st.just(pattern_changed_signal("case_x")),
    ),
    min_size=1,
    max_size=5,
)
actions = st.sampled_from(list(ProposalAction))


@settings(max_examples=500, deadline=None)
@given(results=rule_results, signals=pattern_signals)
def test_patterns_never_lower_a_verdict(
    results: list[RuleResult], signals: list[CautionSignal]
) -> None:
    without = verdicts_of(results, ())
    with_patterns = verdicts_of(results, signals)
    assert with_patterns >= without
    assert with_patterns >= VerdictKind.REQUIRE_APPROVAL  # never ALLOW with a pattern present


@settings(max_examples=500, deadline=None)
@given(first=actions, second=actions)
def test_patterns_never_make_the_action_less_cautious(
    first: ProposalAction, second: ProposalAction
) -> None:
    result = chooser().choose(proposal(first), proposal(second))
    assert rank(result.action) >= rank(first)
    assert result.pattern_influence.changed_recommendation == (rank(result.action) > rank(first))


@settings(max_examples=200, deadline=None)
@given(first=actions)
def test_no_pattern_ever_turns_a_case_into_a_credit_or_an_uphold(first: ProposalAction) -> None:
    """Whatever pass 2 says, a credit or an uphold survives only if pass 1 chose it on the
    case's own evidence - the pattern cannot be the reason (agent-design.md section 7)."""
    for second in (ProposalAction.PROPOSE_CREDIT, ProposalAction.UPHOLD_CHARGE):
        result = chooser().choose(proposal(first), proposal(second))
        if result.action in (ProposalAction.PROPOSE_CREDIT, ProposalAction.UPHOLD_CHARGE):
            assert rank(result.action) >= rank(first)
            assert result.action is first or rank(second) > rank(first)


def test_a_pattern_signal_cannot_say_allow() -> None:
    with pytest.raises(ValidationError):
        CautionSignal(
            kind="pattern_match",
            min_verdict=VerdictKind.ALLOW,
            reason="pattern_match: says it is fine",
            source_ref="pat_x",
        )
