"""The EventLog port (kernel-interfaces.md section 6.2, I8).

Append-only. There is deliberately no update and no delete method: the audit trail is only
worth having if nothing can quietly rewrite it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dispute_agent.kernel.domain.events import Event
from dispute_agent.kernel.domain.ids import CaseId, EventId


class EventLog(Protocol):
    def append(self, event: Event) -> Event:
        """Assigns seq = the case's last seq + 1 and returns the event carrying it.

        Raises DuplicateEvent if event.dedupe_key is set and already used, NotFound if the
        case does not exist.
        """
        ...

    def list_for_case(self, case_id: CaseId, after_seq: int = 0) -> Sequence[Event]:
        """Ordered by seq ascending."""
        ...

    def get(self, event_id: EventId) -> Event:
        """Raises NotFound."""
        ...
