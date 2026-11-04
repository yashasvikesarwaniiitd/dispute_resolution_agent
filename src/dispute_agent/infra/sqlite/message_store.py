"""Inbound seller messages (schema.sql `messages`, direction IN).

A reply is recorded here when dispatch hands it to its case. Three things read it back:
routing (a later reply in the same conversation goes to the same case), the decision deadline
(T29 closes a case as "no response" only if nothing arrived after the window opened - a
seller who replied is never closed as silent, D19), and the next gather round, which takes
the replies not yet interpreted and reads them (the reply round).

The body is not stored here: the inbox keeps the whole inbound event, and this row keeps a
hash and a pointer to it. One copy of untrusted text, in one place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select, update

from dispute_agent.infra.sqlite.mapping import from_iso, to_iso
from dispute_agent.infra.sqlite.tables import messages
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import CaseId, InboxId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.inbound import SellerReplyReceived


@dataclass(frozen=True)
class InboundMessage:
    id: str
    case_id: CaseId
    inbox_id: InboxId
    at: datetime
    interpreted_round: int | None


class SqliteMessageStore:
    """Implements the dispatcher's and the reply round's message port."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def record_inbound(
        self, message_id: str, case_id: CaseId, inbox_id: InboxId, reply: SellerReplyReceived
    ) -> bool:
        """Record a reply once. False when this message was already recorded (a redelivery)."""
        exists = self._conn.execute(
            select(messages.c.id).where(
                and_(
                    messages.c.channel == "outlook",
                    messages.c.direction == "IN",
                    messages.c.external_message_id == reply.external_message_id,
                )
            )
        ).first()
        if exists is not None:
            return False
        self._conn.execute(
            messages.insert().values(
                id=message_id,
                case_id=str(case_id),
                direction="IN",
                inbox_id=str(inbox_id),
                channel="outlook",
                external_message_id=reply.external_message_id,
                internet_message_id=reply.internet_message_id or None,
                conversation_id=reply.conversation_id,
                from_addr=reply.from_addr,
                to_addr="",
                subject=reply.subject.text[:300],
                body_hash=payload_hash(reply.body.text),
                at=to_iso(reply.received_at),
            )
        )
        return True

    def case_for_conversation(self, conversation_id: str) -> CaseId | None:
        row = self._conn.execute(
            select(messages.c.case_id)
            .where(messages.c.conversation_id == conversation_id)
            .order_by(messages.c.at.desc())
        ).first()
        return None if row is None else CaseId(row[0])

    def inbound_after(self, case_id: CaseId, after: datetime) -> int:
        """Replies received after `after` (the decision deadline's anchor, D19)."""
        total = self._conn.execute(
            select(func.count())
            .select_from(messages)
            .where(
                and_(
                    messages.c.case_id == str(case_id),
                    messages.c.direction == "IN",
                    messages.c.at > to_iso(after),
                )
            )
        ).scalar_one()
        return int(total)

    def uninterpreted(self, case_id: CaseId) -> Sequence[InboundMessage]:
        rows = self._conn.execute(
            select(messages)
            .where(
                and_(
                    messages.c.case_id == str(case_id),
                    messages.c.direction == "IN",
                    messages.c.interpreted_round.is_(None),
                )
            )
            .order_by(messages.c.at.asc())
        ).mappings()
        return [_to_message(row) for row in rows]

    def in_round(self, case_id: CaseId, gather_round: int) -> Sequence[InboundMessage]:
        rows = self._conn.execute(
            select(messages)
            .where(
                and_(
                    messages.c.case_id == str(case_id),
                    messages.c.direction == "IN",
                    messages.c.interpreted_round == gather_round,
                )
            )
            .order_by(messages.c.at.asc())
        ).mappings()
        return [_to_message(row) for row in rows]

    def mark_interpreted(self, message_id: str, gather_round: int) -> None:
        self._conn.execute(
            update(messages)
            .where(messages.c.id == message_id)
            .values(interpreted_round=gather_round)
        )


def _to_message(row: object) -> InboundMessage:
    m = row  # a RowMapping
    return InboundMessage(
        id=m["id"],  # type: ignore[index]
        case_id=CaseId(m["case_id"]),  # type: ignore[index]
        inbox_id=InboxId(m["inbox_id"]),  # type: ignore[index]
        at=from_iso(m["at"]),  # type: ignore[index]
        interpreted_round=m["interpreted_round"],  # type: ignore[index]
    )


__all__ = ["InboundMessage", "SqliteMessageStore"]
