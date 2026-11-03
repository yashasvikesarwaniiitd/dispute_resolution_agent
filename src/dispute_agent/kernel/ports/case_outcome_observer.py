"""The CaseOutcomeObserver port — HOOK C, the write side (D3).

Called after commit, at least once per outcome id, so an implementation must be idempotent on
it. Its failure never affects the case: cross-case learning sits downstream of the decision
and must never be able to change one.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.patterns import CaseOutcome


class CaseOutcomeObserver(Protocol):
    def on_outcome(self, outcome: CaseOutcome) -> None:
        """Idempotent on outcome.id. The null adapter does nothing."""
        ...
