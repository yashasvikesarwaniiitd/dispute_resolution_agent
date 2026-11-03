"""The InputScreen port (kernel-interfaces.md section 6.4, I15).

Advisory, always. A flag can only add caution; a clean result never relaxes a rule, and that
asymmetry is the whole safety argument for having a screen at all.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.enums import ScreenKind
from dispute_agent.kernel.domain.reasoning import ScreenResult
from dispute_agent.kernel.domain.values import UntrustedText


class InputScreen(Protocol):
    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult:
        """Raises ModuleUnavailable only.

        Any other failure returns a result flagged "screen_error", which adds caution: a
        screen that broke is a reason to be more careful, not less.
        """
        ...
