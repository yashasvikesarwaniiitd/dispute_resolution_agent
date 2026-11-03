"""The approvals module, switched off (I6, I3).

Disabling this does not disable approval — it disables *asking*. The approvals table is the
record either way (ADR 0013), so a credit still cannot happen without a stored approval; it
is simply that nobody will ever be told to make one. Policy catches that at planning and
escalates `module_unavailable:approvals` rather than letting a case wait for a review request
that was never sent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.ids import ApprovalId

MODULE = "approvals"


class DisabledApprovalService:
    """Implements ApprovalService."""

    def request(self, approval: Approval) -> None:
        raise ModuleUnavailable(MODULE)

    def withdraw(self, approval_id: ApprovalId, reason: str) -> None:
        raise ModuleUnavailable(MODULE)
