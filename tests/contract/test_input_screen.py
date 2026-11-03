"""Contract: InputScreen (kernel-interfaces.md 6.4, I15).

Advisory, always. The suite's sharpest case is the one that looks like a non-test: a screen
that fails must return flags, not a clean result. "Clean" means somebody looked, and every
control downstream is entitled to read it that way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending, ready

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import InputScreen


def _rules_based() -> Any:
    from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen

    return RulesBasedInputScreen()


ADAPTERS = [
    # The stub exists, but it finds nothing until Phase 4 gives it rules, so the cases that
    # matter would all be vacuous. The suite wakes up with the rules.
    ready("rules_based", _rules_based, phase="Phase 4 (card P4-08)"),
    pending("foundry_guardrails", phase="Phase 4b (card P4B-05)", integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def screen(request: pytest.FixtureRequest) -> InputScreen:
    return cast("InputScreen", build_or_skip(request.param))


class TestTheResultShape:
    def test_it_names_the_adapter_that_produced_it(self, screen: InputScreen) -> None:
        """So a flag can be traced to what raised it."""

    def test_clean_text_produces_no_flags(self, screen: InputScreen) -> None: ...


class TestAdvisoryOnly:
    @pytest.mark.skip(reason="injection corpus lands in Phase 4 (card P4-08)")
    def test_every_corpus_item_raises_at_least_one_flag(self, screen: InputScreen) -> None:
        """test-charter.md section 5, twelve items."""

    @pytest.mark.skip(reason="injection corpus lands in Phase 4 (card P4-08)")
    def test_a_screen_failure_returns_screen_error_not_a_clean_result(
        self, screen: InputScreen
    ) -> None:
        """A screen that broke is a reason to be more careful, not less."""

    @pytest.mark.skip(reason="caller-side assertion; the policy engine lands in Phase 1")
    def test_a_clean_result_relaxes_nothing_downstream(self, screen: InputScreen) -> None:
        """I15's real content, and it is a property of the caller rather than of any screen.

        Carried here from card P0-06, which said P0-10 would assert it. P0-10 asserts the
        structural half — CautionSignal cannot lower a verdict — but the behavioural half
        needs a policy engine to be relaxed, and there is not one until Phase 1 (P1-06).
        """
