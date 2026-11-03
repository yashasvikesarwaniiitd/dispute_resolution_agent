"""The ApprovalStore port (kernel-interfaces.md section 6.2, ADR 0013, I3, I13).

The kernel's approvals table is the source of truth about who said yes to what. Not the
review console, not a message from it, not an `ApprovalService` — those notify and display.
A decision that could arrive through a port would be a second source of truth, and the one
that is easiest to fake is the one an attacker reaches for.

Three preconditions live here rather than in a caller, because a caller that forgot one
would produce a record that looks exactly like a valid approval:

- a decision can only be made once, on a PENDING approval;
- APPROVED and REJECTED must name a **human** decider (ADR 0018);
- an approval is single-use — `consume` refuses a second action.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from dispute_agent.kernel.domain.actions import Approval, ApprovalRequest
from dispute_agent.kernel.domain.enums import ApprovalDecision
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, CaseId, IntentId
from dispute_agent.kernel.domain.models import Actor


class ApprovalStore(Protocol):
    def add(self, request: ApprovalRequest) -> Approval:
        """Creates a PENDING approval.

        Raises DomainValidationError if a PENDING approval already exists for the intent:
        two open requests for one message means two chances to say yes.
        """
        ...

    def get(self, approval_id: ApprovalId) -> Approval:
        """Raises NotFound."""
        ...

    def decide(
        self,
        approval_id: ApprovalId,
        decision: ApprovalDecision,
        by: Actor | None,
        at: datetime,
        note: str,
    ) -> Approval:
        """Record a decision. Raises DomainValidationError if it is not allowed.

        `by` is the identity the console established for the session, never a value from a
        request body (ADR 0018). The store cannot tell the difference, which is why the
        rule is written down in both places.
        """
        ...

    def consume(self, approval_id: ApprovalId, action_id: ActionId) -> None:
        """Bind this approval to the one action that used it.

        Raises DomainValidationError if it is not APPROVED or is already used. One yes is
        one send (I13).
        """
        ...

    def for_intent(self, intent_id: IntentId) -> Approval | None:
        """The approval covering this intent, if there is one."""
        ...

    def pending(self, case_id: CaseId | None = None) -> Sequence[Approval]:
        """Everything awaiting a decision, newest last. The review queue reads this."""
        ...

    def unnotified(self) -> Sequence[Approval]:
        """PENDING with `notified_at` null. The runner retries these."""
        ...

    def mark_notified(self, approval_id: ApprovalId, at: datetime) -> None: ...
