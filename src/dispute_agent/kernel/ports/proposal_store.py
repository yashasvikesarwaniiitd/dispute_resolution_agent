"""The ProposalStore port: every proposal, kept (CLAUDE.md section 9, I8's "why").

The state machine records *that* a case moved; this records *why* - what the reasoner
proposed, with which prompt, version and hash and which model, on which briefing. An LLM
proposal without those fields is refused by the database itself (schema.sql CHECK).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.reasoning import Proposal


class ProposalStore(Protocol):
    def add(self, proposal: Proposal) -> None: ...

    def for_case(self, case_id: CaseId) -> Sequence[Proposal]:
        """Oldest first."""
        ...
