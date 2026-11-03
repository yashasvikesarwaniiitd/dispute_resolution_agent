"""The Ledger port (kernel-interfaces.md section 6.4).

Simulated throughout this project (I11): no real money moves. The port is still shaped like a
real one — write, look up, read back — because the executor's guarantees are what is being
built here, and they have to hold against a ledger that can fail or answer twice.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from dispute_agent.kernel.domain.external import (
    CreditRequest,
    LedgerEntry,
    TransactionRef,
    TransactionSnapshot,
)
from dispute_agent.kernel.domain.ids import IdempotencyKey


class Ledger(Protocol):
    idempotent_writes: bool
    lookup_supported: bool

    def post_credit(self, req: CreditRequest, key: IdempotencyKey) -> TransactionRef:
        """Raises as MessageChannel.send."""
        ...

    def find_transaction(self, key: IdempotencyKey) -> TransactionRef | None:
        """Raises as MessageChannel.find_sent."""
        ...

    def get_transaction(self, ref: TransactionRef) -> TransactionSnapshot:
        """Read-back (I2). Raises as MessageChannel.get_message."""
        ...

    def get_entries(self, shipment_ref: str) -> Sequence[LedgerEntry]:
        """Every entry on the shipment, for the outstanding-charge limit in R16."""
        ...
