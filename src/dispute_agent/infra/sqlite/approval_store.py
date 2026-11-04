"""ApprovalStore on SQLite (kernel-interfaces.md 6.2, ADR 0013, I3, I13).

This is the record of who said yes to what, and the only one. Everything it refuses, it
refuses with a `WHERE` clause rather than a read-then-write, because the interesting
failures here are races: two tabs open on the same approval, a decision arriving twice, a
retry consuming an approval a moment after the first attempt did.

**The decision is conditional.** `UPDATE ... WHERE decision = 'PENDING'` changes one row or
none, so a second decision on the same approval cannot overwrite the first. Reading the row,
checking it, then writing would leave a gap wide enough for the second tab.

**Consumption is conditional too.** `WHERE used_by_action_id IS NULL` is what makes one yes
one send. The database also has a unique index on that column, so even a bug here cannot
bind one approval to two actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from dispute_agent.infra.sqlite.mapping import from_iso, optional_from_iso, to_iso
from dispute_agent.infra.sqlite.tables import approvals as approvals_table
from dispute_agent.kernel.domain.actions import Approval, ApprovalRequest
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    MessageKind,
)
from dispute_agent.kernel.domain.errors import DomainValidationError, NotFound
from dispute_agent.kernel.domain.ids import ApprovalId, CaseId, IntentId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.ids import ActionId

#: The only decisions a person can make. EXPIRED and SUPERSEDED are the system's, and they
#: travel a different path (the runner and a transition effect).
HUMAN_DECISIONS = (ApprovalDecision.APPROVED, ApprovalDecision.REJECTED)


class SqliteApprovalStore:
    """Implements ApprovalStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    # -- creating --------------------------------------------------------------------------

    def add(self, request: ApprovalRequest) -> Approval:
        existing = self._conn.execute(
            select(approvals_table.c.id)
            .where(approvals_table.c.intent_id == str(request.intent_id))
            .where(approvals_table.c.decision == ApprovalDecision.PENDING.value)
        ).one_or_none()
        if existing is not None:
            # Two open requests for one message means two chances to say yes, and the
            # second one would not be bound to the text the first reviewer saw.
            raise DomainValidationError(
                f"intent {request.intent_id} already has a pending approval"
            )

        self._conn.execute(
            approvals_table.insert().values(
                id=str(request.approval_id),
                case_id=str(request.case_id),
                intent_id=str(request.intent_id),
                subject=request.subject.value,
                payload_hash=str(request.payload_hash),
                amount_paise=request.amount.to_paise() if request.amount else None,
                message_kind=request.message_kind.value if request.message_kind else None,
                requested_at=to_iso(request.requested_at),
                expires_at=to_iso(request.expires_at),
                decision=ApprovalDecision.PENDING.value,
                note="",
            )
        )
        return self.get(request.approval_id)

    # -- reading ---------------------------------------------------------------------------

    def get(self, approval_id: ApprovalId) -> Approval:
        row = (
            self._conn.execute(
                select(approvals_table).where(approvals_table.c.id == str(approval_id))
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound(f"no approval {approval_id}")
        return _to_approval(row)

    def for_intent(self, intent_id: IntentId) -> Approval | None:
        row = (
            self._conn.execute(
                select(approvals_table)
                .where(approvals_table.c.intent_id == str(intent_id))
                .order_by(approvals_table.c.requested_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return _to_approval(row) if row is not None else None

    def pending(self, case_id: CaseId | None = None) -> Sequence[Approval]:
        query = select(approvals_table).where(
            approvals_table.c.decision == ApprovalDecision.PENDING.value
        )
        if case_id is not None:
            query = query.where(approvals_table.c.case_id == str(case_id))
        rows = self._conn.execute(query.order_by(approvals_table.c.requested_at)).mappings()
        return [_to_approval(row) for row in rows]

    def for_case(self, case_id: CaseId) -> Sequence[Approval]:
        """Every approval on the case, oldest first - for the timeline and the demo."""
        rows = self._conn.execute(
            select(approvals_table)
            .where(approvals_table.c.case_id == str(case_id))
            .order_by(approvals_table.c.requested_at, approvals_table.c.id)
        ).mappings()
        return [_to_approval(row) for row in rows]

    def unnotified(self) -> Sequence[Approval]:
        rows = (
            self._conn.execute(
                select(approvals_table)
                .where(approvals_table.c.decision == ApprovalDecision.PENDING.value)
                .where(approvals_table.c.notified_at.is_(None))
                .order_by(approvals_table.c.requested_at)
            )
            .mappings()
            .all()
        )
        return [_to_approval(row) for row in rows]

    # -- deciding ---------------------------------------------------------------------------

    def decide(
        self,
        approval_id: ApprovalId,
        decision: ApprovalDecision,
        by: Actor | None,
        at: datetime,
        note: str,
    ) -> Approval:
        """One decision, once. Refused by the WHERE clause rather than by a prior read."""
        if decision in HUMAN_DECISIONS and (by is None or by.kind is not ActorKind.HUMAN):
            # ADR 0018. A yes recorded against "system" is a yes nobody gave.
            raise DomainValidationError(f"{decision.value} must name a human decider, got {by!r}")
        if decision is ApprovalDecision.PENDING:
            raise DomainValidationError("PENDING is not a decision")

        result = self._conn.execute(
            approvals_table.update()
            .where(approvals_table.c.id == str(approval_id))
            # The condition that makes it once. A read-check-write would leave a gap wide
            # enough for a second browser tab.
            .where(approvals_table.c.decision == ApprovalDecision.PENDING.value)
            .values(
                decision=decision.value,
                # A person's decision names them and when. The system's (EXPIRED, SUPERSEDED)
                # carries neither - the model refuses a decider on them - and its moment is
                # on the APPROVAL_SUPERSEDED event written with it.
                decided_by=by.id if by and decision in HUMAN_DECISIONS else None,
                decided_at=to_iso(at) if decision in HUMAN_DECISIONS else None,
                note=note[:500],
            )
        )
        if result.rowcount == 0:
            current = self.get(approval_id)
            raise DomainValidationError(
                f"approval {approval_id} is already {current.decision.value}"
            )
        return self.get(approval_id)

    def consume(self, approval_id: ApprovalId, action_id: ActionId) -> None:
        """Bind the approval to the one action that used it. One yes, one send."""
        result = self._conn.execute(
            approvals_table.update()
            .where(approvals_table.c.id == str(approval_id))
            .where(approvals_table.c.decision == ApprovalDecision.APPROVED.value)
            .where(approvals_table.c.used_by_action_id.is_(None))
            .values(used_by_action_id=str(action_id))
        )
        if result.rowcount == 0:
            current = self.get(approval_id)
            raise DomainValidationError(
                f"approval {approval_id} cannot be used: it is {current.decision.value}"
                + (
                    f" and already used by {current.used_by_action_id}"
                    if current.used_by_action_id
                    else ""
                )
            )

    def mark_notified(self, approval_id: ApprovalId, at: datetime) -> None:
        self._conn.execute(
            approvals_table.update()
            .where(approvals_table.c.id == str(approval_id))
            .values(notified_at=to_iso(at))
        )


def _to_approval(row: Any) -> Approval:
    decided_by = row["decided_by"]
    return Approval(
        id=ApprovalId(row["id"]),
        request=ApprovalRequest(
            approval_id=ApprovalId(row["id"]),
            case_id=CaseId(row["case_id"]),
            intent_id=IntentId(row["intent_id"]),
            subject=ApprovalSubject(row["subject"]),
            payload_hash=row["payload_hash"],
            amount=Money.from_paise(row["amount_paise"])
            if row["amount_paise"] is not None
            else None,
            message_kind=MessageKind(row["message_kind"]) if row["message_kind"] else None,
            requested_at=from_iso(row["requested_at"]),
            expires_at=from_iso(row["expires_at"]),
        ),
        decision=ApprovalDecision(row["decision"]),
        decided_by=_actor(decided_by),
        decided_at=optional_from_iso(row["decided_at"]),
        note=row["note"] or "",
        used_by_action_id=row["used_by_action_id"],
        notified_at=optional_from_iso(row["notified_at"]),
    )


def _actor(stored: str | None) -> Actor | None:
    """Rebuild the decider. `human:<id>` is the only shape a person's decision has."""
    if not stored:
        return None
    kind = ActorKind.HUMAN if stored.startswith("human:") else ActorKind.SYSTEM
    return Actor(kind=kind, id=stored)


__all__ = ["HUMAN_DECISIONS", "SqliteApprovalStore"]
