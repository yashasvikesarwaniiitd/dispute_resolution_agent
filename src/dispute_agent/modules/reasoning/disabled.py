"""The reasoning module, switched off (I6).

Three ports, because reasoning does three separable things: propose a next step, read a
reply, draft a message. All three raise, and the orchestrator's answer differs by step — the
investigate step falls back to the pack's rules-only baseline with `fallback_used = True`,
while a drafter or interpreter that is gone escalates (orchestrator.md section 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import (
        CaseBriefing,
        DraftContext,
        DraftedMessage,
        Proposal,
        ReplyContext,
        ReplyReading,
    )

MODULE = "reasoning"


class DisabledReasoner:
    """Implements Reasoner."""

    def propose(self, briefing: CaseBriefing) -> Proposal:
        raise ModuleUnavailable(MODULE)


class DisabledReplyInterpreter:
    """Implements ReplyInterpreter."""

    def interpret(self, ctx: ReplyContext) -> ReplyReading:
        raise ModuleUnavailable(MODULE)


class DisabledMessageDrafter:
    """Implements MessageDrafter."""

    def draft(self, ctx: DraftContext) -> DraftedMessage:
        raise ModuleUnavailable(MODULE)
