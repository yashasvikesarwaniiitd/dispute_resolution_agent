"""The ReplyInterpreter port (kernel-interfaces.md section 6.4).

Reads a seller's reply and returns what it appears to say. The output is data: a reading is
evidence about a reply, never a decision about the case, and nothing downstream may treat a
claim inside it as a fact (I5).
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.reasoning import ReplyContext, ReplyReading


class ReplyInterpreter(Protocol):
    def interpret(self, ctx: ReplyContext) -> ReplyReading:
        """Output is data only. Same raises as Reasoner.propose."""
        ...
