"""The CaseRepository port (kernel-interfaces.md section 6.2).

Optimistic concurrency, not locks. `save` carries the version it expects and fails if the row
moved underneath it, so two workers handling the same case cannot interleave into a state
neither of them intended.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.models import Case, RelatedCase


class CaseRepository(Protocol):
    def get(self, case_id: CaseId) -> Case:
        """Raises NotFound."""
        ...

    def insert(self, case: Case) -> Case:
        """Pre: case.version == 1.

        Raises ConcurrencyConflict if another open case exists for the same
        (shipment_ref, claim_ref). The unique partial index is what makes intake
        deduplication a database guarantee rather than a check with a race in it.
        """
        ...

    def save(self, case: Case, expected_version: int) -> Case:
        """UPDATE ... WHERE id = ? AND version = expected_version.

        Post: the returned case has version == expected_version + 1. Raises
        ConcurrencyConflict if no row matched, NotFound if the id does not exist.
        """
        ...

    def find_open(self, shipment_ref: str, claim_ref: str) -> Case | None:
        """The case for this pair in a working state, if any (at most one, by schema)."""
        ...

    def find_latest(self, shipment_ref: str, claim_ref: str) -> Case | None:
        """The most recently opened case for the pair, in any state."""
        ...

    def list_in_states(self, states: Collection[CaseState], limit: int = 500) -> Sequence[Case]:
        """For restart recovery. Ordered by updated_at ascending."""
        ...

    def related(self, case: Case, limit: int = 10) -> Sequence[RelatedCase]:
        """Other cases with the same seller_ref or shipment_ref, newest first, excluding case."""
        ...
