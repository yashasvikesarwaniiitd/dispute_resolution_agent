"""The rules-based input screen — a stub for now (I15).

It returns no flags. That is honest at this point: there are no rules yet, so it finds
nothing, and Phase 4 fills it in against the twelve-item injection corpus.

Being advisory is what makes a stub safe to ship. A flag can only add caution and a clean
result never relaxes anything, so a screen that finds nothing leaves every other control
exactly where it was. That property belongs to the *caller*, not to this class — P0-10
asserts it, because a screen cannot promise something about code it does not run.

Note the difference from `DisabledInputScreen`, which raises. This one looked and found
nothing; that one was not there. Only this one may say "clean".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.reasoning import ScreenResult

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.enums import ScreenKind
    from dispute_agent.kernel.domain.values import UntrustedText

MODULE = "input_screen"
ADAPTER = "rules_based"


class RulesBasedInputScreen:
    """Implements InputScreen."""

    def screen(self, text: UntrustedText, kind: ScreenKind) -> ScreenResult:
        return ScreenResult(kind=kind, flags=(), blocked=False, adapter=ADAPTER)
