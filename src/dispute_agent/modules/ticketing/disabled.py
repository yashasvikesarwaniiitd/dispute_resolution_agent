"""The ticketing module, switched off (I6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.external import IssueRef, IssueSnapshot, IssueSpec, OpsFinding
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "ticketing"


class DisabledTicketingSystem:
    """Implements TicketingSystem."""

    idempotent_writes = False
    lookup_supported = False

    def upsert_issue(self, spec: IssueSpec, key: IdempotencyKey) -> IssueRef:
        raise ModuleUnavailable(MODULE)

    def find_issue(self, key: IdempotencyKey) -> IssueRef | None:
        raise ModuleUnavailable(MODULE)

    def get_issue(self, ref: IssueRef) -> IssueSnapshot:
        raise ModuleUnavailable(MODULE)

    def fetch_findings(self, since: datetime) -> Sequence[OpsFinding]:
        raise ModuleUnavailable(MODULE)
