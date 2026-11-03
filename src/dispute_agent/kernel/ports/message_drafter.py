"""The MessageDrafter port (kernel-interfaces.md section 6.4).

Writes words, never numbers of its own: every number in a draft must trace to a fact the
assessment produced (I9), and the recipient is set by the system, never by the draft (R07).
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.reasoning import DraftContext, DraftedMessage


class MessageDrafter(Protocol):
    def draft(self, ctx: DraftContext) -> DraftedMessage:
        """Same raises as Reasoner.propose.

        A non-null `blocked_reason` means "do not send" — the drafter refusing is a result,
        not an error, and the caller has to read it rather than catch it.
        """
        ...
