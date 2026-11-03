"""CaseRepository on SQLite (kernel-interfaces.md 6.2, ADR 0008).

Optimistic concurrency, not locks held across work. `save` carries the version it read and
the UPDATE matches on it, so a second writer whose view is stale changes zero rows and is
told, rather than silently overwriting a decision someone else already acted on.

The database backs this up twice over: `tr_cases_version` refuses an update that does not
increment by exactly one, and `tr_cases_legal_transition` refuses a state pair that is not in
`legal_transitions`. Both would catch a write that bypassed this class entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import and_, select, update

from dispute_agent.infra.sqlite.mapping import case_to_row, row_to_case
from dispute_agent.infra.sqlite.tables import OPEN_STATES
from dispute_agent.infra.sqlite.tables import cases as cases_table
from dispute_agent.kernel.domain.errors import ConcurrencyConflict, NotFound
from dispute_agent.kernel.domain.models import Case, RelatedCase
from dispute_agent.kernel.domain.values import Money

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.enums import CaseState
    from dispute_agent.kernel.domain.ids import CaseId


class SqliteCaseRepository:
    """Implements CaseRepository. Used only inside a unit of work."""

    def __init__(
        self, connection: Connection, on_save: Callable[[CaseId, int], None] | None = None
    ) -> None:
        self._conn = connection
        # Lets the unit of work name the case if the commit itself hits the version
        # trigger, which is the one conflict `save` cannot catch in its WHERE clause.
        self._on_save = on_save

    def get(self, case_id: CaseId) -> Case:
        row = self._conn.execute(
            select(cases_table).where(cases_table.c.id == str(case_id))
        ).one_or_none()
        if row is None:
            raise NotFound(f"no case {case_id}")
        return row_to_case(row)

    def insert(self, case: Case) -> Case:
        if case.version != 1:
            raise ConcurrencyConflict(case.id, 1)
        try:
            self._conn.execute(cases_table.insert().values(**case_to_row(case)))
        except Exception as exc:
            # The unique partial index is what makes "one open case per shipment and claim" a
            # guarantee rather than a check with a race in it, so its failure is the answer,
            # not an error to hide.
            if "UNIQUE" in str(exc).upper():
                # The spec names ConcurrencyConflict for this (section 6.2), so a caller
                # handles a duplicate open case the same way it handles a lost race: re-read
                # and look again. `DuplicateCase` would read better and would be a change to
                # a frozen interface for a word.
                raise ConcurrencyConflict(case.id, 1) from exc
            raise
        return case

    def save(self, case: Case, expected_version: int) -> Case:
        """UPDATE ... WHERE id = ? AND version = ?. Zero rows matched means someone else won."""
        updated = case.model_copy(update={"version": expected_version + 1})
        row = case_to_row(updated)
        row.pop("id")
        if self._on_save is not None:
            self._on_save(case.id, expected_version)
        result = self._conn.execute(
            update(cases_table)
            .where(
                and_(
                    cases_table.c.id == str(case.id),
                    cases_table.c.version == expected_version,
                )
            )
            .values(**row)
        )
        if result.rowcount == 0:
            exists = self._conn.execute(
                select(cases_table.c.version).where(cases_table.c.id == str(case.id))
            ).one_or_none()
            if exists is None:
                raise NotFound(f"no case {case.id}")
            raise ConcurrencyConflict(case.id, expected_version)
        return updated

    def find_open(self, shipment_ref: str, claim_ref: str) -> Case | None:
        row = self._conn.execute(
            select(cases_table).where(
                and_(
                    cases_table.c.shipment_ref == shipment_ref,
                    cases_table.c.claim_ref == claim_ref,
                    cases_table.c.state.in_(OPEN_STATES),
                )
            )
        ).one_or_none()
        return None if row is None else row_to_case(row)

    def find_latest(self, shipment_ref: str, claim_ref: str) -> Case | None:
        row = self._conn.execute(
            select(cases_table)
            .where(
                and_(
                    cases_table.c.shipment_ref == shipment_ref,
                    cases_table.c.claim_ref == claim_ref,
                )
            )
            .order_by(cases_table.c.opened_at.desc())
            .limit(1)
        ).one_or_none()
        return None if row is None else row_to_case(row)

    def list_in_states(self, states: Collection[CaseState], limit: int = 500) -> Sequence[Case]:
        """Oldest first: restart recovery works through the backlog, not the newest arrivals."""
        rows = self._conn.execute(
            select(cases_table)
            .where(cases_table.c.state.in_([s.value for s in states]))
            .order_by(cases_table.c.updated_at.asc())
            .limit(limit)
        ).all()
        return [row_to_case(row) for row in rows]

    def related(self, case: Case, limit: int = 10) -> Sequence[RelatedCase]:
        """Outcome only. A reasoner that could read a related case's evidence would be
        reasoning about a dispute nobody asked it to decide."""
        rows = self._conn.execute(
            select(
                cases_table.c.id,
                cases_table.c.state,
                cases_table.c.closed_at,
                cases_table.c.shipment_ref,
            )
            .where(
                and_(
                    cases_table.c.id != str(case.id),
                    (cases_table.c.seller_ref == case.facts.seller_ref)
                    | (cases_table.c.shipment_ref == case.facts.shipment_ref),
                )
            )
            .order_by(cases_table.c.opened_at.desc())
            .limit(limit)
        ).all()
        from dispute_agent.infra.sqlite.mapping import optional_from_iso
        from dispute_agent.kernel.domain.enums import CaseState as _State
        from dispute_agent.kernel.domain.ids import CaseId as _CaseId

        return [
            RelatedCase(
                case_id=_CaseId(row[0]),
                state=_State(row[1]),
                closed_at=optional_from_iso(row[2]),
                discrepancy=self._discrepancy(_CaseId(row[0])),
            )
            for row in rows
        ]

    def _discrepancy(self, case_id: CaseId) -> Money | None:
        """From the latest assessment, if there is one. Phase 2 fills these in."""
        from dispute_agent.infra.sqlite.tables import assessments

        row = self._conn.execute(
            select(assessments.c.discrepancy_paise)
            .where(assessments.c.case_id == str(case_id))
            .order_by(assessments.c.gather_round.desc())
            .limit(1)
        ).one_or_none()
        if row is None or row[0] is None:
            return None
        return Money.from_paise(int(row[0]))
