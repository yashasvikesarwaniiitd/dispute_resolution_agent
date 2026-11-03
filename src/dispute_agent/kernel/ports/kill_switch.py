"""The KillSwitch port (kernel-interfaces.md section 6.1, I10).

Read fresh on every call, never cached: the point of a kill switch is that flipping it stops
the next write, not the next restart. It is checked inside the executor rather than at the
entry point, so no code path reaches an external write without passing it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KillSwitch(Protocol):
    def is_engaged(self) -> bool:
        """True if external writes must stop (I10).

        Engaged if the KILL_SWITCH setting is true or control_flags.kill_switch is "1". If the
        flag cannot be read at all, returns True: not knowing whether we are allowed to write
        is a reason to stop, not a reason to carry on. Raises nothing.
        """
        ...
