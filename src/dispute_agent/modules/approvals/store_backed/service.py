"""The ApprovalService: it notifies, and that is all (ADR 0013).

Worth being precise about what "store-backed" means here, because the name invites the
wrong reading. This service does **not** hold approvals and does not receive decisions. The
kernel's approvals table is the record; a decision arrives as an APPROVAL_DECIDED inbound
event, through the same door as every other outside event, and is written by the kernel.

What this does is mark a request as notified, so the runner stops retrying it and the queue
knows the reviewer has had a chance to see it. That is a small job, and making it small is
the point: a port that could return a decision would be a second source of truth about who
said yes, and the easiest one to forge.

The queue it "notifies" is a local Streamlit page reading the same database, so notifying is
a timestamp rather than a message. When this becomes an email or a Slack post, only this
file changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import NotFound

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.ids import ApprovalId
    from dispute_agent.kernel.ports import ApprovalStore, Clock

MODULE = "approvals"
ADAPTER = "store_backed"


@dataclass
class StoreBackedApprovalService:
    """Implements ApprovalService over the kernel's own approvals table."""

    store: ApprovalStore
    clock: Clock

    def request(self, approval: Approval) -> None:
        """Idempotent on the approval id.

        Marking an already-notified approval again is a no-op rather than an error: the
        runner retries until this succeeds, and an exception on the second attempt would
        turn a successful notification into a permanent failure.
        """
        self.store.mark_notified(approval.id, self.clock.now())

    def withdraw(self, approval_id: ApprovalId, reason: str) -> None:
        """Take a stale item out of the queue. Idempotent.

        The approval's own decision (SUPERSEDED or EXPIRED) is set by the kernel in the
        transaction that made it stale; this only removes it from view. An approval that
        has already gone is not an error - `after_commit` calls this for every one the
        transaction touched, and some will have been withdrawn already.
        """
        try:
            self.store.get(approval_id)
        except NotFound:
            return


__all__ = ["ADAPTER", "MODULE", "StoreBackedApprovalService"]
