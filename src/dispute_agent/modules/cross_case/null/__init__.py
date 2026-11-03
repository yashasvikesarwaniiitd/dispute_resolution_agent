"""The empty box (D3, I7, FLOW.md section 6).

`cross_case` is the one module whose disabled wiring is a Null adapter rather than a Disabled
one (orchestrator.md section 5: "Null adapter: no patterns, no signal"). That is not an
inconsistency — it follows from what the module does. Every other module answers a question
the case needs answered, so its absence has to be noticed. Cross-case intelligence only ever
*adds* caution, so its absence removes nothing: a case with no patterns is exactly a case
decided on its own evidence, which is the normal state of affairs through Phase 6.

Until Phase 7 these two are what is wired in, and connecting the real module must change no
line in `kernel/`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.patterns import CaseOutcome, PatternRecord

MODULE = "cross_case"


class NullPatternContextProvider:
    """Implements PatternContextProvider. There are no patterns, and that is not an error."""

    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        return ()


class NullOutcomeObserver:
    """Implements CaseOutcomeObserver. Nobody is learning from outcomes yet."""

    def on_outcome(self, outcome: CaseOutcome) -> None:
        return None
