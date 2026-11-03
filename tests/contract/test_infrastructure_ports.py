"""Contract: Clock, IdGenerator and Tracer.

These three already have adapters and unit tests (card P0-09), so most of the suite runs
today rather than waiting. It is here anyway because the rule is one suite per port run
against every adapter, and these are the ports where the fake being kinder than the real
thing would be least visible: a FakeIdGenerator that accepted any prefix, or a FakeClock that
allowed time to run backwards, would let a whole phase of scenarios pass on behaviour the
system will never actually have.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator, UlidIdGenerator
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import ID_PREFIXES, is_valid_id
from dispute_agent.modules.tracing.noop import NoopTracer
from tests.contract.conftest import build_or_skip, params, pending, ready

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import Clock, IdGenerator, Tracer

CLOCKS = [
    ready("system", SystemClock, phase="Phase 0"),
    ready("fake", lambda: FakeClock(datetime(2026, 3, 1, 9, 30, tzinfo=UTC)), phase="Phase 0"),
]

GENERATORS = [
    ready("ulid", UlidIdGenerator, phase="Phase 0"),
    ready("fake", FakeIdGenerator, phase="Phase 0"),
]

TRACERS = [
    ready("noop", NoopTracer, phase="Phase 0"),
    pending("otel_foundry", phase="Phase 4b (card P4B-06)", integration=True),
]


@pytest.fixture(params=params(CLOCKS))
def clock(request: pytest.FixtureRequest) -> Clock:
    return cast("Clock", build_or_skip(request.param))


@pytest.fixture(params=params(GENERATORS))
def ids(request: pytest.FixtureRequest) -> IdGenerator:
    return cast("IdGenerator", build_or_skip(request.param))


@pytest.fixture(params=params(TRACERS))
def tracer(request: pytest.FixtureRequest) -> Tracer:
    return cast("Tracer", build_or_skip(request.param))


class TestClock:
    def test_now_is_timezone_aware_utc(self, clock: Clock) -> None:
        now = clock.now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_it_never_goes_backwards(self, clock: Clock) -> None:
        readings = [clock.now() for _ in range(20)]
        assert readings == sorted(readings)


class TestIdGenerator:
    @pytest.mark.parametrize("prefix", sorted(ID_PREFIXES))
    def test_every_known_prefix_produces_a_valid_id(self, ids: IdGenerator, prefix: str) -> None:
        assert is_valid_id(ids.new_id(prefix), prefix)

    def test_an_unknown_prefix_is_refused(self, ids: IdGenerator) -> None:
        """The fake must be exactly as strict, or a bug waits until production to appear."""
        with pytest.raises(DomainValidationError):
            ids.new_id("shipment")

    def test_ids_are_unique(self, ids: IdGenerator) -> None:
        assert len({ids.new_id("evt") for _ in range(500)}) == 500

    def test_ids_sort_in_creation_order(self, ids: IdGenerator) -> None:
        issued = [ids.new_id("evt") for _ in range(100)]
        assert issued == sorted(issued)


class TestTracer:
    def test_a_span_is_a_context_manager(self, tracer: Tracer) -> None:
        with tracer.span("step.investigate", case_id="case_01HQ"):
            pass

    def test_a_span_never_raises(self, tracer: Tracer) -> None:
        """The port's own contract: a broken tracer degrades to a no-op rather than
        failing a case. Observability must not change what the system decides."""
        with tracer.span("step.investigate"):
            pass

    def test_an_exception_inside_a_span_still_propagates(self, tracer: Tracer) -> None:
        """Not raising of its own accord is different from swallowing the caller's."""
        with pytest.raises(RuntimeError, match="boom"), tracer.span("step.investigate"):
            raise RuntimeError("boom")
