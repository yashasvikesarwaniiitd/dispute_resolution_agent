"""The ledger, switched off (I6, I3).

`get_entries` raises along with the rest, and that matters more than it looks. R16 caps a
credit at the outstanding charge, which is computed from these entries; a disabled ledger
returning an empty sequence would read as "nothing has been charged or credited" and make
the cap meaningless. money-rules.md says an unreadable ledger refuses the credit with
`ledger_state_unknown`, which is only reachable if this says so rather than guessing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.external import (
        CreditRequest,
        LedgerEntry,
        TransactionRef,
        TransactionSnapshot,
    )
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "ledger"


class DisabledLedger:
    """Implements Ledger."""

    idempotent_writes = False
    lookup_supported = False

    def post_credit(self, req: CreditRequest, key: IdempotencyKey) -> TransactionRef:
        raise ModuleUnavailable(MODULE)

    def find_transaction(self, key: IdempotencyKey) -> TransactionRef | None:
        raise ModuleUnavailable(MODULE)

    def get_transaction(self, ref: TransactionRef) -> TransactionSnapshot:
        raise ModuleUnavailable(MODULE)

    def get_entries(self, shipment_ref: str) -> Sequence[LedgerEntry]:
        raise ModuleUnavailable(MODULE)
