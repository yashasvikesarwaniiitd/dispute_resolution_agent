"""The outcome outbox (HOOK C, orchestrator.md section 6, schema.sql `outcome_outbox`).

A case's outcome is written here in the same transaction as the move into its terminal
state, and delivered to the CaseOutcomeObserver afterwards by the runner. Delivering inside
the transaction would let a failing observer roll back a case that really did finish; never
delivering would leave Phase 7 with nothing to learn from. An outbox is the usual answer.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from dispute_agent.infra.sqlite.mapping import to_iso
from dispute_agent.infra.sqlite.tables import outcome_outbox
from dispute_agent.kernel.domain.patterns import CaseOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import Connection


class SqliteOutcomeOutbox:
    """Implements OutcomeOutbox. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, outcome: CaseOutcome, now: datetime) -> None:
        self._conn.execute(
            outcome_outbox.insert().values(
                id=str(outcome.id),
                case_id=str(outcome.case_id),
                state=outcome.state.value,
                resolved=1 if outcome.resolved else 0,
                outcome_json=json.dumps(outcome.model_dump(mode="json"), sort_keys=True),
                created_at=to_iso(now),
            )
        )

    def undelivered(self, limit: int = 100) -> Sequence[CaseOutcome]:
        rows = self._conn.execute(
            select(outcome_outbox.c.outcome_json)
            .where(outcome_outbox.c.delivered_at.is_(None))
            .order_by(outcome_outbox.c.created_at.asc())
            .limit(limit)
        ).all()
        return [CaseOutcome.model_validate(json.loads(row[0])) for row in rows]

    def mark_delivered(self, outcome_id: str, now: datetime) -> None:
        self._conn.execute(
            update(outcome_outbox)
            .where(outcome_outbox.c.id == outcome_id)
            .values(delivered_at=to_iso(now))
        )

    def mark_failed(self, outcome_id: str, error: str) -> None:
        self._conn.execute(
            update(outcome_outbox)
            .where(outcome_outbox.c.id == outcome_id)
            .values(attempts=outcome_outbox.c.attempts + 1, last_error=error[:300])
        )


__all__ = ["SqliteOutcomeOutbox"]
