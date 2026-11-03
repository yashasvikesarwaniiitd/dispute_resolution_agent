"""EventLog on SQLite (kernel-interfaces.md 6.2, I8).

Append-only, and deliberately boring. There is no update method and no delete method here,
there is none on the port, and `tr_events_no_update` / `tr_events_no_delete` refuse both at
the database. Three layers saying the same thing is not redundancy — the audit trail is the
one record that has to survive somebody being clever.

`append` assigns the sequence number by reading the case's last one inside the step's
transaction. That read-then-write is safe because the transaction is BEGIN IMMEDIATE
(ADR 0008) and `UNIQUE (case_id, seq)` is there if it ever is not: a race fails loudly rather
than reordering the diary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from dispute_agent.infra.sqlite.mapping import event_to_row, row_to_event
from dispute_agent.infra.sqlite.tables import cases as cases_table
from dispute_agent.infra.sqlite.tables import events as events_table
from dispute_agent.kernel.domain.errors import DuplicateEvent, NotFound
from dispute_agent.kernel.domain.ids import InboxId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.events import Event
    from dispute_agent.kernel.domain.ids import CaseId, EventId


class SqliteEventLog:
    """Implements EventLog. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def append(self, event: Event) -> Event:
        case_exists = self._conn.execute(
            select(cases_table.c.id).where(cases_table.c.id == str(event.case_id))
        ).one_or_none()
        if case_exists is None:
            raise NotFound(f"no case {event.case_id} to append an event to")

        if event.dedupe_key is not None:
            already = self._conn.execute(
                select(events_table.c.id).where(events_table.c.dedupe_key == str(event.dedupe_key))
            ).one_or_none()
            if already is not None:
                # DuplicateEvent names its second argument `existing_inbox_id` because the
                # inbox is where duplicates are normally caught (ADR 0006). Here the existing
                # row is an event; the id is what a caller needs either way.
                raise DuplicateEvent(event.dedupe_key, InboxId(already[0]))

        last = self._conn.execute(
            select(func.coalesce(func.max(events_table.c.seq), 0)).where(
                events_table.c.case_id == str(event.case_id)
            )
        ).scalar_one()
        stored = event.model_copy(update={"seq": int(last) + 1})
        self._conn.execute(events_table.insert().values(**event_to_row(stored)))
        return stored

    def list_for_case(self, case_id: CaseId, after_seq: int = 0) -> Sequence[Event]:
        rows = self._conn.execute(
            select(events_table)
            .where((events_table.c.case_id == str(case_id)) & (events_table.c.seq > after_seq))
            .order_by(events_table.c.seq.asc())
        ).all()
        return [row_to_event(row) for row in rows]

    def get(self, event_id: EventId) -> Event:
        row = self._conn.execute(
            select(events_table).where(events_table.c.id == str(event_id))
        ).one_or_none()
        if row is None:
            raise NotFound(f"no event {event_id}")
        return row_to_event(row)
