"""Timers as rows (orchestrator.md section 6, schema.sql `timers`).

Nothing sleeps in memory. A reminder due in 48 hours is a row with a `due_at`, and the runner
asks "what is due?" on each tick. That is the whole reason a restart resumes: the deadline
was never held in a process that could die.

`EVIDENCE_RETRY` carries `gather_round`, and only that kind does — the schema enforces it.
D41 limits retries to three *per round*, so a later round must not start already at the
limit; without the column the count would be per case and the second round would get none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, func, select, update

from dispute_agent.infra.sqlite.mapping import optional_from_iso, to_iso
from dispute_agent.infra.sqlite.tables import timers as timers_table
from dispute_agent.kernel.domain.actions import Timer
from dispute_agent.kernel.domain.enums import CaseState, TimerKind
from dispute_agent.kernel.domain.errors import DomainValidationError, NotFound
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, TimerId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy import Connection


class SqliteTimerStore:
    """Implements TimerStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def schedule(self, timer: Timer) -> Timer:
        """Arm a timer. At most one active per case and kind (ux_timers_active).

        A second one is refused by the index rather than by a check, which matters because
        two steps arming the same reminder is exactly the kind of race a check would lose.
        """
        if (timer.gather_round is not None) != (timer.kind is TimerKind.EVIDENCE_RETRY):
            raise DomainValidationError(
                "gather_round belongs to EVIDENCE_RETRY alone (D41, schema.sql)"
            )
        self._conn.execute(
            timers_table.insert().values(
                id=str(timer.id),
                case_id=str(timer.case_id),
                kind=timer.kind.value,
                due_at=to_iso(timer.due_at),
                anchor_at=to_iso(timer.anchor_at),
                armed_in_state=timer.armed_in_state.value,
                armed_at_version=timer.armed_at_version,
                gather_round=timer.gather_round,
                correlation_id=str(timer.correlation_id),
                created_at=to_iso(timer.created_at),
            )
        )
        return timer

    def due(self, now: datetime, limit: int = 100) -> Sequence[Timer]:
        """Armed timers whose moment has come, oldest first."""
        rows = self._conn.execute(
            select(timers_table)
            .where(
                and_(
                    timers_table.c.due_at <= to_iso(now),
                    timers_table.c.fired_at.is_(None),
                    timers_table.c.cancelled_at.is_(None),
                )
            )
            .order_by(timers_table.c.due_at.asc())
            .limit(limit)
        ).all()
        return [_to_timer(row) for row in rows]

    def active_for_case(self, case_id: CaseId) -> Sequence[Timer]:
        rows = self._conn.execute(
            select(timers_table).where(
                and_(
                    timers_table.c.case_id == str(case_id),
                    timers_table.c.fired_at.is_(None),
                    timers_table.c.cancelled_at.is_(None),
                )
            )
        ).all()
        return [_to_timer(row) for row in rows]

    def mark_fired(self, timer_id: TimerId, now: datetime) -> None:
        result = self._conn.execute(
            update(timers_table)
            .where(
                and_(
                    timers_table.c.id == str(timer_id),
                    timers_table.c.fired_at.is_(None),
                    timers_table.c.cancelled_at.is_(None),
                )
            )
            .values(fired_at=to_iso(now))
        )
        if result.rowcount == 0:
            # Already fired or cancelled. Not an error: two schedulers racing on one timer is
            # exactly what the dedupe key on TIMER_FIRED exists for.
            raise NotFound(f"timer {timer_id} is not armed")

    def cancel(self, timer_id: TimerId, now: datetime, reason: str) -> None:
        self._conn.execute(
            update(timers_table)
            .where(
                and_(
                    timers_table.c.id == str(timer_id),
                    timers_table.c.fired_at.is_(None),
                    timers_table.c.cancelled_at.is_(None),
                )
            )
            .values(cancelled_at=to_iso(now), cancel_reason=reason)
        )

    def cancel_kinds(
        self, case_id: CaseId, kinds: Sequence[TimerKind], now: datetime, reason: str
    ) -> int:
        """Cancel the named kinds for a case. Leaving a state cancels what it armed."""
        if not kinds:
            return 0
        result = self._conn.execute(
            update(timers_table)
            .where(
                and_(
                    timers_table.c.case_id == str(case_id),
                    timers_table.c.kind.in_([k.value for k in kinds]),
                    timers_table.c.fired_at.is_(None),
                    timers_table.c.cancelled_at.is_(None),
                )
            )
            .values(cancelled_at=to_iso(now), cancel_reason=reason)
        )
        return int(result.rowcount)

    def cancel_all(self, case_id: CaseId, now: datetime, reason: str) -> int:
        return self.cancel_kinds(case_id, list(TimerKind), now, reason)

    def retries_this_round(self, case_id: CaseId, gather_round: int) -> int:
        """How many EVIDENCE_RETRY timers this round has used (D41).

        Counted per round rather than per case, which is the whole reason the column exists:
        a second round of gathering starts with its own three, not with none left.
        """
        total = self._conn.execute(
            select(func.count())
            .select_from(timers_table)
            .where(
                and_(
                    timers_table.c.case_id == str(case_id),
                    timers_table.c.kind == TimerKind.EVIDENCE_RETRY.value,
                    timers_table.c.gather_round == gather_round,
                )
            )
        ).scalar_one()
        return int(total)


def _to_timer(row: object) -> Timer:
    mapping = row._mapping  # type: ignore[attr-defined]
    return Timer(
        id=TimerId(mapping["id"]),
        case_id=CaseId(mapping["case_id"]),
        kind=TimerKind(mapping["kind"]),
        due_at=optional_from_iso(mapping["due_at"]),
        anchor_at=optional_from_iso(mapping["anchor_at"]),
        armed_in_state=CaseState(mapping["armed_in_state"]),
        armed_at_version=mapping["armed_at_version"],
        gather_round=mapping["gather_round"],
        correlation_id=CorrelationId(mapping["correlation_id"]),
        created_at=optional_from_iso(mapping["created_at"]),
        fired_at=optional_from_iso(mapping["fired_at"]),
        consumed_at=optional_from_iso(mapping["consumed_at"]),
        voided_at=optional_from_iso(mapping["voided_at"]),
        cancelled_at=optional_from_iso(mapping["cancelled_at"]),
        cancel_reason=mapping["cancel_reason"],
    )
