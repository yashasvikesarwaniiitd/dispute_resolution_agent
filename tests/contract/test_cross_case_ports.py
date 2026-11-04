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


#: The case being decided in every provider test: claim CHG-now, opened as case_now.
CASE_NOW = "case_now"


def _null_provider() -> Any:
    from dispute_agent.modules.cross_case.null import NullPatternContextProvider

    return NullPatternContextProvider()


def _null_observer() -> Any:
    from dispute_agent.modules.cross_case.null import NullOutcomeObserver

    return NullOutcomeObserver()


def _sql_provider(*, broken: bool = False) -> Any:
    """A store holding two patterns the case being decided matches.

    - hub HUB-01: case_a, case_b, case_c and the case itself. The others alone meet the
      threshold, so it is returned - without the case in its related ids.
    - seller SELL-0009: case_a, case_b and the case itself. Without the case there are two,
      below the threshold: returning it would let the case be evidence for itself.
    """
    from dispute_agent.modules.cross_case.sql_context import (
        PatternRepository,
        SqlPatternContextProvider,
    )
    from tests.support.patterns import add_case, add_pattern, new_engine

    engine = new_engine()
    for case_id in ("case_a", "case_b", "case_c"):
        add_case(engine, case_id)
    add_case(engine, CASE_NOW, claim="CHG-now", seller="SELL-0009")
    repo = PatternRepository(engine)
    add_pattern(repo, "pat_hub", "hub", "HUB-01", ["case_a", "case_b", "case_c", CASE_NOW])
    add_pattern(repo, "pat_seller", "seller", "SELL-0009", ["case_a", "case_b", CASE_NOW])

    def keys(shipment_ref: str) -> dict[str, str]:
        if broken:
            raise LookupError("the shipment attributes could not be read")
        return {"hub": "HUB-01", "scanner": "SCN-01-1", "sku": "SKU-1"}

    return SqlPatternContextProvider(repository=repo, keys=keys)


PROVIDERS = [
    ready("null", _null_provider, phase="Phase 0"),
    ready("sql_context", _sql_provider, phase="Phase 7 (card P7-02)"),
]

#: The same adapters with their store failing. The null adapter has no store to fail.
BROKEN_PROVIDERS = [
    pending("null", phase="never: the null adapter has no store that could fail"),
    ready("sql_context", lambda: _sql_provider(broken=True), phase="Phase 7 (card P7-02)"),
]


def _sql_observer(*, broken: bool = False) -> Any:
    from dispute_agent.infra.clock import FakeClock
    from dispute_agent.modules.cross_case.analyzer.observer import SqlOutcomeObserver
    from dispute_agent.modules.cross_case.sql_context import PatternRepository
    from tests.support.patterns import T0, add_case, new_engine

    engine = new_engine()
    add_case(engine, CASE_NOW, claim="CHG-now")

    def keys(shipment_ref: str) -> dict[str, str]:
        return {"hub": "HUB-01"}

    class BrokenStore(PatternRepository):
        def record_outcome(self, *args: Any, **kwargs: Any) -> bool:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("INSERT", {}, Exception("disk I/O error"))

    repository = BrokenStore(engine) if broken else PatternRepository(engine)
    observer = SqlOutcomeObserver(repository, keys, FakeClock(T0))
    observer.engine = engine  # type: ignore[attr-defined]  # for the assertions below
    return observer


OBSERVERS = [
    ready("null", _null_observer, phase="Phase 0"),
    ready("sql_context", _sql_observer, phase="Phase 7 (card P7-07)"),
]

BROKEN_OBSERVERS = [
    pending("null", phase="never: the null adapter has no store that could fail"),
    ready("sql_context", lambda: _sql_observer(broken=True), phase="Phase 7 (card P7-07)"),
]


def an_outcome() -> Any:
    from dispute_agent.kernel.domain.enums import CaseState
    from dispute_agent.kernel.domain.ids import CaseId, OutcomeId
    from dispute_agent.kernel.domain.patterns import CaseOutcome
    from tests.support.patterns import T0, facts

    return CaseOutcome(
        id=OutcomeId("out_01"),
        case_id=CaseId(CASE_NOW),
        state=CaseState.WITHDRAWN,
        resolved=False,
        facts=facts(),
        closed_at=T0,
    )


def _rows(observer: Any, table: str) -> int | None:
    engine = getattr(observer, "engine", None)
    if engine is None:
        return None
    raw = engine.raw_connection()
    try:
        return int(raw.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        raw.close()


@pytest.fixture(params=params(PROVIDERS))
def provider(request: pytest.FixtureRequest) -> PatternContextProvider:
    return cast("PatternContextProvider", build_or_skip(request.param))


@pytest.fixture(params=params(BROKEN_PROVIDERS))
def broken_provider(request: pytest.FixtureRequest) -> PatternContextProvider:
    return cast("PatternContextProvider", build_or_skip(request.param))


@pytest.fixture(params=params(OBSERVERS))
def observer(request: pytest.FixtureRequest) -> CaseOutcomeObserver:
    return cast("CaseOutcomeObserver", build_or_skip(request.param))


@pytest.fixture(params=params(BROKEN_OBSERVERS))
def broken_observer(request: pytest.FixtureRequest) -> CaseOutcomeObserver:
    return cast("CaseOutcomeObserver", build_or_skip(request.param))


class TestPatternContextProvider:
    def test_it_returns_a_sequence_of_patterns(self, provider: PatternContextProvider) -> None:
        from dispute_agent.kernel.domain.patterns import PatternRecord
        from tests.support.patterns import facts

        found = provider.patterns_for(facts())
        assert len(found) <= 5
        assert all(isinstance(p, PatternRecord) for p in found)
        stamps = [p.last_seen_at for p in found]
        assert stamps == sorted(stamps, reverse=True), "newest first"

    def test_it_never_returns_a_pattern_from_the_case_being_decided(
        self, provider: PatternContextProvider
    ) -> None:
        """No circular confirmation: a case may not be evidence for the pattern that shaped
        it. CaseOutcome.used_pattern_ids is what makes this checkable."""
        from tests.support.patterns import facts

        for pattern in provider.patterns_for(facts()):
            assert CASE_NOW not in pattern.related_case_ids
            assert len(pattern.related_case_ids) >= 3 or pattern.related_case_ids == ()
        assert "pat_seller" not in {str(p.id) for p in provider.patterns_for(facts())}

    def test_a_failure_raises_transient_rather_than_returning_empty(
        self, broken_provider: PatternContextProvider
    ) -> None:
        """Empty means "no patterns apply". A failure means "we do not know", and the
        orchestrator turns it into a pattern_context_unavailable signal."""
        from dispute_agent.kernel.domain.errors import TransientPortError
        from tests.support.patterns import facts

        with pytest.raises(TransientPortError):
            broken_provider.patterns_for(facts())


class TestCaseOutcomeObserver:
    def test_the_same_outcome_twice_is_recorded_once(self, observer: CaseOutcomeObserver) -> None:
        """At least once per outcome id, so it must be idempotent on it."""
        outcome = an_outcome()
        observer.on_outcome(outcome)
        observer.on_outcome(outcome)
        recorded = _rows(observer, "pattern_outcomes")
        assert recorded in (None, 1)

    def test_its_failure_does_not_reach_the_case(
        self, broken_observer: CaseOutcomeObserver
    ) -> None:
        """Learning sits downstream of the decision and must never change one."""
        from dispute_agent.kernel.domain.errors import TransientPortError

        before = _rows(broken_observer, "cases"), _rows(broken_observer, "events")
        with pytest.raises(TransientPortError):  # recorded on the outbox row and retried
            broken_observer.on_outcome(an_outcome())
        assert (_rows(broken_observer, "cases"), _rows(broken_observer, "events")) == before
