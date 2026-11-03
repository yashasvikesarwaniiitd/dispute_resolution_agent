"""The transactional inbox (ADR 0006, schema.sql `inbox`).

Every inbound event lands here first and is deduplicated by `<source>:<source_event_id>`
before anything else happens. That ordering is the whole point: the mail poller can deliver
the same message twice, the approval console can be double-clicked, and two schedulers can
race on one timer — and in each case the second arrival is refused by a UNIQUE index rather
than by a check that could be raced.

The inbox is not the audit trail. Its status changes as an event is routed and processed; the
`events` table is the append-only record, and nothing here is ever the answer to "what
happened to this case?".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update

from dispute_agent.infra.sqlite.mapping import to_iso
from dispute_agent.infra.sqlite.tables import inbox as inbox_table
from dispute_agent.kernel.domain.enums import InboxStatus
from dispute_agent.kernel.domain.errors import DuplicateEvent, NotFound
from dispute_agent.kernel.domain.ids import InboxId

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.events import InboundEvent
    from dispute_agent.kernel.domain.ids import CaseId, EventId


class SqliteInboxStore:
    """Implements InboxStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def accept(self, event: InboundEvent, received_at: datetime) -> InboxId:
        """Insert with status PENDING, or raise DuplicateEvent naming the original.

        The duplicate is looked up first rather than caught from the UNIQUE violation,
        because the caller needs the existing id to answer with — a repeat submission gets
        the same answer as the first, which is what makes the endpoint safe to retry.
        """
        key = str(event.dedupe_key)
        existing = self._conn.execute(
            select(inbox_table.c.id).where(inbox_table.c.dedupe_key == key)
        ).one_or_none()
        if existing is not None:
            raise DuplicateEvent(event.dedupe_key, InboxId(existing[0]))

        inbox_id = InboxId(f"inb_{_suffix(key)}")
        self._conn.execute(
            inbox_table.insert().values(
                id=str(inbox_id),
                dedupe_key=key,
                source=event.source,
                source_event_id=event.source_event_id,
                event_type=event.type.value,
                event_json=event.model_dump_json(),
                received_at=to_iso(received_at),
                status=InboxStatus.PENDING.value,
                case_id=None if event.case_id is None else str(event.case_id),
            )
        )
        return inbox_id

    def get(self, inbox_id: InboxId) -> dict[str, object]:
        row = self._conn.execute(
            select(inbox_table).where(inbox_table.c.id == str(inbox_id))
        ).one_or_none()
        if row is None:
            raise NotFound(f"no inbox row {inbox_id}")
        return dict(row._mapping)

    def mark_processed(self, inbox_id: InboxId, case_id: CaseId, event_id: EventId) -> None:
        """PROCESSED needs both ids: the schema refuses the status without them, because an
        event that was processed but belongs to no case is a contradiction."""
        self._set(inbox_id, InboxStatus.PROCESSED, case_id=case_id, event_id=event_id)

    def mark_deferred(self, inbox_id: InboxId, case_id: CaseId, event_id: EventId) -> None:
        self._set(inbox_id, InboxStatus.DEFERRED, case_id=case_id, event_id=event_id)

    def mark_unroutable(self, inbox_id: InboxId, reason: str) -> None:
        """No case could be found. The row stays, so nothing is lost and a human can look."""
        self._conn.execute(
            update(inbox_table)
            .where(inbox_table.c.id == str(inbox_id))
            .values(status=InboxStatus.UNROUTABLE.value, last_error=reason)
        )

    def mark_failed(self, inbox_id: InboxId, reason: str) -> None:
        self._conn.execute(
            update(inbox_table)
            .where(inbox_table.c.id == str(inbox_id))
            .values(
                status=InboxStatus.FAILED.value,
                last_error=reason,
                attempts=inbox_table.c.attempts + 1,
            )
        )

    def deferred_for_case(self, case_id: CaseId) -> list[dict[str, object]]:
        """Held events, oldest first, for redelivery when the case moves on."""
        rows = self._conn.execute(
            select(inbox_table)
            .where(
                (inbox_table.c.case_id == str(case_id))
                & (inbox_table.c.status == InboxStatus.DEFERRED.value)
            )
            .order_by(inbox_table.c.received_at.asc())
        ).all()
        return [dict(row._mapping) for row in rows]

    def _set(
        self, inbox_id: InboxId, status: InboxStatus, *, case_id: CaseId, event_id: EventId
    ) -> None:
        self._conn.execute(
            update(inbox_table)
            .where(inbox_table.c.id == str(inbox_id))
            .values(status=status.value, case_id=str(case_id), event_id=str(event_id))
        )


def _suffix(key: str) -> str:
    """A deterministic 26-character suffix, so a replay produces the same inbox id.

    Derived from the dedupe key rather than from a generator: the id is then a function of
    the event, which makes a duplicate submission idempotent all the way down.
    """
    import hashlib

    digest = hashlib.sha256(key.encode("utf-8")).hexdigest().upper()
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    value = int(digest, 16)
    out = []
    for _ in range(26):
        out.append(alphabet[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))
