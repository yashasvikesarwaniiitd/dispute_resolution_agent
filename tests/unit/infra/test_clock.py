"""Card P0-09: Clock and IdGenerator adapters.

The FakeClock tests are what make every timer scenario in this project possible. A case that
waits 48 hours for a reminder, 72 for a decision and 120 for an ops timeout has to be
runnable in a millisecond and give the same answer every time, and that only works if time
moves exactly when a test says so and not otherwise.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator, UlidIdGenerator
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import ID_PREFIXES, is_valid_id

START = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


class TestFakeClock:
    def test_it_does_not_move_on_its_own(self) -> None:
        """Two reads without an advance are the same instant, so a step cannot straddle a
        boundary and make a test flap once in fifty runs."""
        clock = FakeClock(START)
        assert clock.now() == clock.now() == START

    def test_it_advances_only_when_told(self) -> None:
        clock = FakeClock(START)
        clock.advance(timedelta(hours=48))
        assert clock.now() == START + timedelta(hours=48)

    def test_advancing_is_cumulative(self) -> None:
        clock = FakeClock(START)
        clock.advance_hours(48)
        clock.advance_hours(24)
        assert clock.now() == START + timedelta(hours=72)

    def test_it_refuses_to_go_backwards(self) -> None:
        clock = FakeClock(START)
        with pytest.raises(DomainValidationError, match="backwards"):
            clock.advance(timedelta(hours=-1))

    def test_setting_it_backwards_is_refused_too(self) -> None:
        clock = FakeClock(START)
        with pytest.raises(DomainValidationError, match="backwards"):
            clock.set(START - timedelta(seconds=1))

    def test_it_can_jump_forwards(self) -> None:
        clock = FakeClock(START)
        assert clock.set(START + timedelta(days=7)) == START + timedelta(days=7)

    def test_a_naive_start_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="naive"):
            FakeClock(datetime(2026, 3, 1, 9, 30))


class TestSystemClock:
    def test_it_is_timezone_aware_and_utc(self) -> None:
        now = SystemClock().now()
        assert now.tzinfo is not None
        assert now.utcoffset() == timedelta(0)

    def test_it_never_goes_backwards(self) -> None:
        """A clock that steps back would make a timer fall due twice."""
        clock = SystemClock()
        readings = [clock.now() for _ in range(50)]
        assert readings == sorted(readings)

    def test_a_backwards_jump_is_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulates NTP stepping the clock back mid-run."""
        clock = SystemClock()
        first = clock.now()

        import dispute_agent.infra.clock as module

        class _Stepped:
            @staticmethod
            def now(tz: object = None) -> datetime:
                return first - timedelta(seconds=30)

        monkeypatch.setattr(module, "datetime", _Stepped)
        assert clock.now() == first


class TestUlidIdGenerator:
    def test_the_format_is_what_the_kernel_validates(self) -> None:
        generated = UlidIdGenerator().new_id("case")
        assert generated.startswith("case_")
        assert is_valid_id(generated, "case")

    @pytest.mark.parametrize("prefix", sorted(ID_PREFIXES))
    def test_every_known_prefix_works(self, prefix: str) -> None:
        assert is_valid_id(UlidIdGenerator().new_id(prefix), prefix)

    def test_an_unknown_prefix_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="prefix"):
            UlidIdGenerator().new_id("shipment")

    def test_ids_are_unique(self) -> None:
        generator = UlidIdGenerator()
        assert len({generator.new_id("evt") for _ in range(2000)}) == 2000

    def test_ids_sort_in_creation_order(self) -> None:
        """The reason for ULIDs over UUID4: an events table read in id order is in the order
        things happened, and two ids minted in the same millisecond still sort correctly."""
        generator = UlidIdGenerator()
        issued = [generator.new_id("evt") for _ in range(500)]
        assert issued == sorted(issued)


class TestFakeIdGenerator:
    def test_it_counts(self) -> None:
        generator = FakeIdGenerator()
        assert generator.new_id("case") == "case_" + "0" * 25 + "1"
        assert generator.new_id("case") == "case_" + "0" * 24 + "02"

    def test_its_ids_pass_the_same_validation(self) -> None:
        """Nothing downstream should be able to tell which generator it is talking to."""
        assert is_valid_id(FakeIdGenerator().new_id("evt"), "evt")

    def test_two_runs_produce_the_same_ids(self) -> None:
        assert [FakeIdGenerator().new_id("evt") for _ in range(3)] == [
            FakeIdGenerator().new_id("evt") for _ in range(3)
        ]

    def test_it_refuses_an_unknown_prefix_too(self) -> None:
        """A fake that is laxer than the real thing hides bugs until production."""
        with pytest.raises(DomainValidationError, match="prefix"):
            FakeIdGenerator().new_id("shipment")

    def test_it_records_what_it_issued(self) -> None:
        generator = FakeIdGenerator()
        generator.new_id("case")
        generator.new_id("evt")
        assert len(generator.issued) == 2
