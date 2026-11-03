"""The Reasoner port (kernel-interfaces.md section 6.4, I1).

The narrowest port in the system, and that is the point. It takes a briefing and returns a
proposal. It holds no store, no channel, no executor and no clock, so "the AI never writes"
is not a rule anyone has to remember — there is nothing here to write with.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal


class Reasoner(Protocol):
    def propose(self, briefing: CaseBriefing) -> Proposal:
        """Receives read-only data only (I1).

        Raises ModuleUnavailable, InvalidModelOutput, BudgetExceeded, GuardrailBlocked,
        AgentDrift.
        """
        ...
