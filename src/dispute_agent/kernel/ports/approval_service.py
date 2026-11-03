"""The ApprovalService port (kernel-interfaces.md section 6.4, ADR 0013).

It notifies the review queue and nothing more. The decision does not come back through this
port — it arrives as an APPROVAL_DECIDED inbound event, and the kernel's own approvals table
is the record (I3). A port that could return a decision would be a second source of truth,
and the one that is easiest to fake is the one an attacker would reach for.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.actions import Approval
from dispute_agent.kernel.domain.ids import ApprovalId


class ApprovalService(Protocol):
    def request(self, approval: Approval) -> None:
        """Idempotent on approval.id. Raises ModuleUnavailable, TransientPortError."""
        ...

    def withdraw(self, approval_id: ApprovalId, reason: str) -> None:
        """Idempotent. Raises ModuleUnavailable, TransientPortError."""
        ...
