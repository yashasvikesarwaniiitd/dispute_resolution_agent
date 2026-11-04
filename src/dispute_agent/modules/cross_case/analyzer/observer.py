"""SqlOutcomeObserver: HOOK C's real adapter (P7-07, D3).

Implements the existing `CaseOutcomeObserver` port; the port does not change. Each closed case
is recorded once (idempotent on the outcome id, because HOOK C is at least once) and becomes
input for the analyzer, the confirmation rule and the recurrence monitor.

It also records which patterns the case **could have been shown**: every pattern on one of the
case's keys that existed before the case closed is linked to it as `informed`. The kernel's
CaseOutcome cannot name the patterns a case used (`used_pattern_ids` stays empty, and Phase 7
changes no kernel line), so the module over-approximates instead: a case that might have been
influenced by a pattern is treated as if it was. That can only remove evidence for a pattern,
never add it - the safe direction for the "no circular confirmation" rule (P7-08).

Failure: it raises, and the runner records the failure on the outbox row and retries. It never
touches a kernel table, so nothing it does can change a case.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from dispute_agent.infra.sqlite.mapping import to_iso
from dispute_agent.kernel.domain.errors import TransientPortError

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.patterns import CaseOutcome
    from dispute_agent.kernel.ports import Clock
    from dispute_agent.modules.cross_case.sql_context import ShipmentKeys
    from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository


@dataclass
class SqlOutcomeObserver:
    """Implements CaseOutcomeObserver."""

    repository: PatternRepository
    keys: ShipmentKeys
    clock: Clock

    def on_outcome(self, outcome: CaseOutcome) -> None:
        now = self.clock.now()
        try:
            credited = None if outcome.credited is None else _paise(outcome.credited.amount)
            self.repository.record_outcome(
                str(outcome.id),
                str(outcome.case_id),
                outcome.state.value,
                outcome.resolved,
                credited,
                outcome.closed_at,
                now,
            )
            self._mark_informed(outcome)
        except (SQLAlchemyError, LookupError, ValueError, OSError) as failed:
            raise TransientPortError(
                f"outcome could not be recorded: {type(failed).__name__}"
            ) from failed

    def _mark_informed(self, outcome: CaseOutcome) -> None:
        keys = dict(self.keys(outcome.facts.shipment_ref))
        keys.setdefault("seller", outcome.facts.seller_ref)
        closed = to_iso(outcome.closed_at)
        for pattern in self.repository.matching(keys.items()):
            if to_iso(pattern.first_seen_at) <= closed:
                self.repository.link(
                    pattern.id, [str(outcome.case_id)], "informed", outcome.closed_at
                )
        # Dismissed patterns are not in `matching` and confirm nothing, so they need no link.


def _paise(amount: object) -> int:
    from decimal import Decimal

    return int((Decimal(str(amount)) * 100).to_integral_value())
