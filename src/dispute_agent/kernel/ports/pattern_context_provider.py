"""The PatternContextProvider port — HOOK A, the read side (D3, I7).

Wired to a null adapter that returns () until Phase 7. The orchestrator already calls it, so
connecting cross-case intelligence later changes nothing in the kernel.

Its failure is not a silent skip: a TransientPortError becomes a `pattern_context_unavailable`
caution signal, which raises the verdict. Not knowing whether a pattern applies is itself a
reason to be more careful.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.patterns import PatternRecord


class PatternContextProvider(Protocol):
    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        """Read-only. The null adapter returns (). Raises TransientPortError."""
        ...
