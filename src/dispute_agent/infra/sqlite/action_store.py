"""ActionStore on SQLite: the record of every side effect (executor.md, ADR 0006, I2, I3, I4).

Every external write - an email, a Jira issue, a credit - is a row here *before* it happens,
and the row is what the executor reads to decide whether a retry is safe. Three things are
enforced by the database as well as by this code, on purpose (schema.sql):

- a credit row cannot exist without an approval that covers it exactly
  (`tr_actions_approval_binding`, and a CHECK);
- one approval, one action (`approvals.used_by_action_id` UNIQUE, consumed here in the same
  transaction as the insert);
- one live credit per shipment and reason (`ux_actions_one_credit`);
- status moves only along the legal edges, and UNKNOWN is resolved only by a person
  (`tr_actions_status`).

**The claim is conditional.** `UPDATE ... WHERE status = 'PLANNED' AND (unclaimed OR lease
expired)` changes one row or none, so two workers can never both write the same action, and
a successful claim increments `attempts` - which executor.md section 5.1 reads.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import and_, func, or_, select, update

from dispute_agent.infra.sqlite.mapping import from_iso, optional_from_iso, to_iso
from dispute_agent.infra.sqlite.tables import action_intents, actions, approvals
from dispute_agent.kernel.domain.actions import Action
from dispute_agent.kernel.domain.enums import ActionStatus, ActionType, ActorKind
from dispute_agent.kernel.domain.errors import ConcurrencyConflict, NotFound
from dispute_agent.kernel.domain.ids import (
    ActionId,
    ApprovalId,
    CaseId,
    IdempotencyKey,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.actions import Approval

#: Statuses that hold a credit's place: it happened, or might have (I4).
LIVE = ("PLANNED", "SENT", "UNKNOWN", "VERIFIED")


class SqliteActionStore:
    """Implements the executor's ActionStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    # -- writing -----------------------------------------------------------------------------

    def insert(self, action: Action) -> Action:
        self._conn.execute(
            actions.insert().values(
                id=str(action.id),
                case_id=str(action.case_id),
                intent_id=str(action.intent_id),
                approval_id=None if action.approval_id is None else str(action.approval_id),
                action_type=action.action_type.value,
                action_seq=action.action_seq,
                idempotency_key=str(action.idempotency_key),
                payload_hash=str(action.payload_hash),
                status=action.status.value,
                attempts=action.attempts,
                planned_at=to_iso(action.planned_at),
                credit_shipment_ref=action.credit_shipment_ref,
                credit_reason_code=action.credit_reason_code,
            )
        )
        if action.approval_id is not None:
            # Consumed in the same transaction as the insert: one yes, one action. The
            # conditional WHERE plus the UNIQUE index mean a second use cannot succeed.
            used = self._conn.execute(
                update(approvals)
                .where(approvals.c.id == str(action.approval_id))
                .where(approvals.c.used_by_action_id.is_(None))
                .values(used_by_action_id=str(action.id))
            )
            if used.rowcount != 1:
                raise ConcurrencyConflict(action.case_id, -1)
        return self.get(action.id)

    def claim(self, action_id: ActionId, worker: str, now: datetime, lease_s: int) -> bool:
        from datetime import timedelta

        result = self._conn.execute(
            update(actions)
            .where(actions.c.id == str(action_id))
            .where(actions.c.status == "PLANNED")
            .where(or_(actions.c.claimed_by.is_(None), actions.c.claim_expires_at < to_iso(now)))
            .values(
                claimed_by=worker,
                claim_expires_at=to_iso(now + timedelta(seconds=lease_s)),
                attempts=actions.c.attempts + 1,
            )
        )
        return result.rowcount == 1

    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action:
        values: dict[str, Any] = {"status": status.value}
        stamp = {
            ActionStatus.SENT: "sent_at",
            ActionStatus.VERIFIED: "verified_at",
            ActionStatus.FAILED: "failed_at",
            ActionStatus.UNKNOWN: "unknown_at",
        }.get(status)
        if stamp is not None:
            values[stamp] = to_iso(now)
        for name, value in fields.items():
            if name in ("sent_at", "verified_at", "failed_at", "unknown_at"):
                values[name] = to_iso(value) if value is not None else None  # type: ignore[arg-type]
            elif name == "verified_by":
                values[name] = _actor_text(value)
            elif name == "verification":
                values["verification_json"] = json.dumps(value, sort_keys=True, default=str)
            else:
                values[name] = value
        if status in (
            ActionStatus.SENT,
            ActionStatus.VERIFIED,
            ActionStatus.FAILED,
            ActionStatus.UNKNOWN,
        ):
            # The write is over (or has been settled); the lease no longer protects anything.
            values["claimed_by"] = None
            values["claim_expires_at"] = None
        self._conn.execute(update(actions).where(actions.c.id == str(action_id)).values(**values))
        return self.get(action_id)

    # -- reading -------------------------------------------------------------------------------

    def get(self, action_id: ActionId) -> Action:
        row = (
            self._conn.execute(select(actions).where(actions.c.id == str(action_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound(f"no action {action_id}")
        return _to_action(row)

    def for_intent(self, intent_id: IntentId) -> Action | None:
        row = (
            self._conn.execute(select(actions).where(actions.c.intent_id == str(intent_id)))
            .mappings()
            .one_or_none()
        )
        return None if row is None else _to_action(row)

    def for_case(self, case_id: CaseId) -> list[Action]:
        rows = self._conn.execute(
            select(actions).where(actions.c.case_id == str(case_id)).order_by(actions.c.action_seq)
        ).mappings()
        return [_to_action(row) for row in rows]

    def find_by_external_ref(self, external_ref: str) -> Action | None:
        """The action that produced this message id or issue key (dispatch routing)."""
        row = (
            self._conn.execute(
                select(actions).where(actions.c.external_ref == external_ref).limit(1)
            )
            .mappings()
            .first()
        )
        return None if row is None else _to_action(row)

    def find_approval_for(self, intent_id: object) -> Approval | None:
        from dispute_agent.infra.sqlite.approval_store import SqliteApprovalStore

        return SqliteApprovalStore(self._conn).for_intent(IntentId(str(intent_id)))

    def live_credits(self, shipment_ref: str, reason_code: str) -> int:
        count = self._conn.execute(
            select(func.count())
            .select_from(actions)
            .where(actions.c.action_type == ActionType.POST_CREDIT.value)
            .where(actions.c.credit_shipment_ref == shipment_ref)
            .where(actions.c.credit_reason_code == reason_code)
            .where(actions.c.status.in_(LIVE))
        ).scalar_one()
        return int(count)

    def credited_on_shipment(self, shipment_ref: str) -> tuple[Money, Money]:
        """(every live credit, the live duplicate_charge credits) on this shipment, any case.

        The amount is read from the intent each action was planned from, so a credit that is
        PLANNED but not yet posted still counts (money-rules.md section 3: netting).
        """
        rows = self._conn.execute(
            select(actions.c.credit_reason_code, action_intents.c.payload_json)
            .select_from(actions.join(action_intents, actions.c.intent_id == action_intents.c.id))
            .where(actions.c.action_type == ActionType.POST_CREDIT.value)
            .where(actions.c.credit_shipment_ref == shipment_ref)
            .where(actions.c.status.in_(LIVE))
        ).all()
        total = duplicate = 0
        for reason, payload_json in rows:
            paise = Money.of(str(json.loads(payload_json)["amount"]["amount"])).to_paise()
            total += paise
            if reason == "duplicate_charge":
                duplicate += paise
        return Money.from_paise(total), Money.from_paise(duplicate)

    def has_verified_credit(self, case_id: object) -> bool:
        count = self._conn.execute(
            select(func.count())
            .select_from(actions)
            .where(actions.c.case_id == str(case_id))
            .where(actions.c.action_type == ActionType.POST_CREDIT.value)
            .where(actions.c.status == "VERIFIED")
        ).scalar_one()
        return int(count) > 0

    def outbound_message_count(self, case_id: object, since: datetime | None = None) -> int:
        query = (
            select(func.count())
            .select_from(actions)
            .where(actions.c.case_id == str(case_id))
            .where(actions.c.action_type == ActionType.SEND_MESSAGE.value)
            .where(actions.c.status.in_(("SENT", "VERIFIED", "UNKNOWN")))
        )
        if since is not None:
            query = query.where(
                and_(actions.c.sent_at.is_not(None), actions.c.sent_at >= to_iso(since))
            )
        return int(self._conn.execute(query).scalar_one())


def _actor_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, Actor):
        return "system" if value.kind is ActorKind.SYSTEM else value.id
    return str(value)


def _to_action(row: Any) -> Action:
    verified = row["verified_by"]
    actor = None
    if verified is not None:
        actor = (
            Actor(kind=ActorKind.SYSTEM, id="system")
            if verified == "system"
            else Actor(kind=ActorKind.HUMAN, id=verified)
        )
    return Action(
        id=ActionId(row["id"]),
        case_id=CaseId(row["case_id"]),
        intent_id=IntentId(row["intent_id"]),
        approval_id=ApprovalId(row["approval_id"]) if row["approval_id"] else None,
        action_type=ActionType(row["action_type"]),
        action_seq=row["action_seq"],
        idempotency_key=IdempotencyKey(row["idempotency_key"]),
        payload_hash=PayloadHash(row["payload_hash"]),
        status=ActionStatus(row["status"]),
        attempts=row["attempts"],
        external_ref=row["external_ref"],
        last_error_code=row["last_error_code"],
        planned_at=from_iso(row["planned_at"]),
        sent_at=optional_from_iso(row["sent_at"]),
        verified_at=optional_from_iso(row["verified_at"]),
        verify_attempts=row["verify_attempts"],
        verify_deadline_at=optional_from_iso(row["verify_deadline_at"]),
        verified_by=actor,
        credit_shipment_ref=row["credit_shipment_ref"],
        credit_reason_code=row["credit_reason_code"],
    )


__all__ = ["SqliteActionStore"]
