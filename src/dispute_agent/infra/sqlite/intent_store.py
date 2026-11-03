"""IntentStore on SQLite (kernel-interfaces.md 6.2, I13).

The payload is stored as JSON alongside its hash, and read back through the same domain
model it was written from. The hash is the thing an approval binds to, so it is stored as
given and never recomputed here: a store that "helpfully" re-hashed on read could make an
edited payload look approved.

The schema adds its own refusals on top: a SEND_MESSAGE intent must name its message kind,
and a POST_CREDIT intent must carry an amount and a reason. Those CHECKs mean a malformed
plan cannot be written even if a caller builds one.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import TypeAdapter
from sqlalchemy import select

from dispute_agent.infra.sqlite.mapping import from_iso, to_iso
from dispute_agent.infra.sqlite.tables import action_intents as intents_table
from dispute_agent.kernel.domain.actions import ActionIntent, ActionPayload
from dispute_agent.kernel.domain.enums import ActionType, IntentSource, IntentStatus
from dispute_agent.kernel.domain.errors import NotFound
from dispute_agent.kernel.domain.ids import AssessmentId, CaseId, IntentId, PayloadHash

if TYPE_CHECKING:
    from sqlalchemy import Connection

_PAYLOAD: TypeAdapter[Any] = TypeAdapter(ActionPayload)


class SqliteIntentStore:
    """Implements IntentStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, intent: ActionIntent) -> None:
        payload = intent.payload
        kind = getattr(payload, "kind", None)
        amount = getattr(payload, "amount", None)
        self._conn.execute(
            intents_table.insert().values(
                id=str(intent.id),
                case_id=str(intent.case_id),
                source_kind=intent.source_kind.value,
                source_ref=intent.source_ref,
                action_type=intent.action_type.value,
                # Only a message has a kind the schema accepts; an issue's "remeasurement"
                # is a payload detail, not a message kind.
                message_kind=kind.value
                if intent.action_type is ActionType.SEND_MESSAGE and kind is not None
                else None,
                payload_json=json.dumps(payload.model_dump(mode="json"), sort_keys=True),
                payload_hash=str(intent.payload_hash),
                amount_paise=amount.to_paise() if amount is not None else None,
                reason_code=getattr(payload, "reason_code", None),
                assessment_id=str(intent.assessment_id) if intent.assessment_id else None,
                status=intent.status.value,
                created_at=to_iso(intent.created_at),
            )
        )

    def get(self, intent_id: IntentId) -> ActionIntent:
        row = (
            self._conn.execute(select(intents_table).where(intents_table.c.id == str(intent_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise NotFound(f"no intent {intent_id}")
        return ActionIntent(
            id=IntentId(row["id"]),
            case_id=CaseId(row["case_id"]),
            source_kind=IntentSource(row["source_kind"]),
            source_ref=row["source_ref"],
            action_type=ActionType(row["action_type"]),
            payload=_PAYLOAD.validate_python(json.loads(row["payload_json"]), strict=False),
            payload_hash=PayloadHash(row["payload_hash"]),
            assessment_id=AssessmentId(row["assessment_id"]) if row["assessment_id"] else None,
            status=IntentStatus(row["status"]),
            created_at=from_iso(row["created_at"]),
        )

    def set_status(self, intent_id: IntentId, status: IntentStatus) -> None:
        result = self._conn.execute(
            intents_table.update()
            .where(intents_table.c.id == str(intent_id))
            .values(status=status.value)
        )
        if result.rowcount == 0:
            raise NotFound(f"no intent {intent_id}")


__all__ = ["SqliteIntentStore"]
