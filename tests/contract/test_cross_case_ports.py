"""Contract: PatternContextProvider and CaseOutcomeObserver (HOOK A and HOOK C, D3, I7).

The null adapters exist and pass trivially, so they are in the suite from today — which is
the useful part. When Phase 7 adds `sql_context`, it runs against cases the null adapter has
already been holding to, and the first thing it has to prove is that it does not change what
a case decides, only how carefully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending, ready

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import CaseOutcomeObserver, PatternContextProvider


def _null_provider() -> Any:
    from dispute_agent.modules.cross_case.null import NullPatternContextProvider

    return NullPatternContextProvider()


def _null_observer() -> Any:
    from dispute_agent.modules.cross_case.null import NullOutcomeObserver

    return NullOutcomeObserver()


PROVIDERS = [
    ready("null", _null_provider, phase="Phase 0"),
    pending("sql_context", phase="Phase 7 (card P7-03)"),
]

OBSERVERS = [
    ready("null", _null_observer, phase="Phase 0"),
    pending("sql_context", phase="Phase 7 (card P7-04)"),
]


@pytest.fixture(params=params(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> PatternContextProvider:
    return cast("PatternContextProvider", build_or_skip(request.param))


@pytest.fixture(params=params(OBSERVERS))
def observer(request: pytest.FixtureRequest) -> CaseOutcomeObserver:
    return cast("CaseOutcomeObserver", build_or_skip(request.param))


class TestPatternContextProvider:
    @pytest.mark.skip(reason="needs a CaseFacts fixture factory; lands with Phase 1 (P1-05)")
    def test_it_returns_a_sequence_of_patterns(self, provider: PatternContextProvider) -> None: ...

    @pytest.mark.skip(reason="needs stored patterns; Phase 7 (card P7-03)")
    def test_it_never_returns_a_pattern_from_the_case_being_decided(
        self, provider: PatternContextProvider
    ) -> None:
        """No circular confirmation: a case may not be evidence for the pattern that shaped
        it. CaseOutcome.used_pattern_ids is what makes this checkable."""

    @pytest.mark.skip(reason="needs a failing store; Phase 7 (card P7-03)")
    def test_a_failure_raises_transient_rather_than_returning_empty(
        self, provider: PatternContextProvider
    ) -> None:
        """Empty means "no patterns apply". A failure means "we do not know", and the
        orchestrator turns it into a pattern_context_unavailable signal."""


class TestCaseOutcomeObserver:
    @pytest.mark.skip(reason="needs a CaseOutcome fixture factory; Phase 1 (P1-09)")
    def test_the_same_outcome_twice_is_recorded_once(self, observer: CaseOutcomeObserver) -> None:
        """At least once per outcome id, so it must be idempotent on it."""

    @pytest.mark.skip(reason="needs a failing store; Phase 7 (card P7-04)")
    def test_its_failure_does_not_reach_the_case(self, observer: CaseOutcomeObserver) -> None:
        """Learning sits downstream of the decision and must never change one."""
