"""The input screen, switched off (I6, I15).

This one raises for a reason worth stating plainly. The obvious alternative — return a clean
result with no flags — would be the single most dangerous adapter in the project: downstream,
a clean screen is evidence that text was looked at and found ordinary, and I15's asymmetry
only holds if "clean" really means that. A screen that is not there must not be able to say
anything about the text.

So it raises, and the investigate step turns that into a `screen_flag` caution signal with
reason `screen_unavailable` at minimum REQUIRE_APPROVAL (orchestrator.md section 5): losing
the screen adds caution rather than removing it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.enums import ScreenKind
    from dispute_agent.kernel.domain.reasoning import ScreenResult
    from dispute_agent.kernel.domain.values import UntrustedText

MODULE = "input_screen"


class DisabledInputScreen:
    """Implements InputScreen."""

    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult:
        raise ModuleUnavailable(MODULE)
