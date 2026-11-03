"""The Clock port (kernel-interfaces.md section 6.1).

Time is a dependency, not an ambient fact. Everything that needs "now" asks this port, so a
test can place a case three days in the past and a timer fires deterministically instead of
whenever the suite happens to run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current UTC time, timezone-aware.

        Post: non-decreasing within a process — the system adapter clamps a backwards jump,
        because a clock that went back would make a timer fall due twice. Raises nothing.
        """
        ...
