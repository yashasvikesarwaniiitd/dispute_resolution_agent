"""The TicketingSystem port (kernel-interfaces.md section 6.4).

The same write-port shape as MessageChannel, with one asymmetry worth knowing: `find_issue`
is best effort. A hit is reliable, a miss is not proof of absence, so a miss alone never
justifies writing again (integrations.md 2.2).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from dispute_agent.kernel.domain.external import IssueRef, IssueSnapshot, IssueSpec, OpsFinding
from dispute_agent.kernel.domain.ids import IdempotencyKey


class TicketingSystem(Protocol):
    idempotent_writes: bool
    lookup_supported: bool

    def upsert_issue(self, spec: IssueSpec, key: IdempotencyKey) -> IssueRef:
        """Raises as MessageChannel.send."""
        ...

    def find_issue(self, key: IdempotencyKey) -> IssueRef | None:
        """Best effort: a hit is reliable, a miss is not. Raises as MessageChannel.find_sent."""
        ...

    def get_issue(self, ref: IssueRef) -> IssueSnapshot:
        """Read-back (I2). Raises as MessageChannel.get_message."""
        ...

    def fetch_findings(self, since: datetime) -> Sequence[OpsFinding]:
        """Polling. Raises TransientPortError, AuthPortError, ModuleUnavailable."""
        ...
