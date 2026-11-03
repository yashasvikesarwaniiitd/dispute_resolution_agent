"""Clock adapters (kernel-interfaces.md section 6.1).

Two of them, and the fake is not an afterthought. Half this system is about time — reminder
at 48 hours, decision at 72, an ops timeout at 120 — and none of that is testable against a
clock that moves on its own. `FakeClock` advances only when a test says so, which is what
makes a scenario that spans five days run in a millisecond and give the same answer twice.

`SystemClock` clamps backwards jumps. A clock that goes back makes a timer fall due a second
time, and the case would act twice on one deadline.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import require_aware


class SystemClock:
    """Implements Clock. Wall-clock UTC, never going backwards within a process."""

    def __init__(self) -> None:
        self._last: datetime | None = None

    def now(self) -> datetime:
        current = datetime.now(UTC)
        if self._last is not None and current < self._last:
            # NTP stepped the clock back, or someone changed it. Hold the previous instant:
            # repeating a moment is survivable, going back and re-firing a timer is not.
            return self._last
        self._last = current
        return current


class FakeClock:
    """Implements Clock. Moves only when told.

    Two calls to `now()` without an `advance` return the same instant, so a step that reads
    the time twice cannot straddle a boundary and make a test flap.
    """

    def __init__(self, start: datetime) -> None:
        self._now = require_aware(start, "clock start")

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise DomainValidationError(f"a clock cannot be moved backwards: {delta}")
        self._now += delta
        return self._now

    def advance_hours(self, hours: float) -> datetime:
        return self.advance(timedelta(hours=hours))

    def set(self, instant: datetime) -> datetime:
        """Jump to an instant, forwards only."""
        target = require_aware(instant, "clock instant")
        if target < self._now:
            raise DomainValidationError(
                f"a clock cannot be set backwards: {target.isoformat()} < {self._now.isoformat()}"
            )
        self._now = target
        return self._now
