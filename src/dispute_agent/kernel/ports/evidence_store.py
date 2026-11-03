"""The EvidenceStore port (kernel-interfaces.md section 6.2).

Used only inside a unit of work: a round's items commit with the case row and the events
that describe them, or none of them do. A half-written round would be worse than no round,
because `assess` would read it as a complete answer with items missing.

`unread_replies` is on the spec's protocol and deliberately absent here. It returns
`MessageRecord`s, and there are no messages until Phase 3; adding a method that returns an
empty sequence would read as "no replies" rather than "replies do not exist yet", which is
the same mistake MISSING-versus-FAILED is about.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.evidence import EvidenceBundle, EvidenceItem
from dispute_agent.kernel.domain.ids import CaseId


class EvidenceStore(Protocol):
    def add(self, item: EvidenceItem) -> None: ...
    def bundle(self, case_id: CaseId, gather_round: int) -> EvidenceBundle:
        """Raises NotFound if the round has no items.
        Not an empty bundle: a round with nothing in it means the gather step never ran,
        and an empty bundle would be assessed as though every source had answered "nothing".
        """
        ...
