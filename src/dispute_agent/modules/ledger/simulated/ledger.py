"""The simulated ledger (P5-01, money-rules.md section 7, D4, I3, I11).

No real money moves in this project, so this is the only ledger there will ever be - which is
why it behaves like a strict real one rather than a convenient one. It has its own SQLite file
(`data/ledger.sqlite3`), separate from the case database, so a bug in case storage cannot fake
a ledger entry, and it shares **no code** with the kernel's money logic: it does its own
arithmetic in paise, from its own rows. Verification (P5-05) is only meaningful if the thing
being verified was produced independently of the thing verifying it.

`post_credit` validates like a real ledger: a positive amount, a known reason, and never more
than is outstanding on the shipment. The same idempotency key twice returns the first
transaction marked `duplicate`, writing nothing; a different key for the same shipment and
reason while one is posted is refused (409).

Failure modes (`failure_mode`, test configuration only, off by default) are exactly the
table in money-rules.md section 7.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    ExternalNotFound,
    ModuleUnavailable,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import LedgerEntry, TransactionRef, TransactionSnapshot
from dispute_agent.kernel.domain.ids import IdempotencyKey
from dispute_agent.kernel.domain.values import Money

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from dispute_agent.kernel.domain.external import CreditRequest

MODULE = "ledger"
KNOWN_REASONS = ("calculation_error", "remeasurement_lower", "duplicate_charge")
FAILURE_MODES = (
    "timeout_before",
    "timeout_after",
    "error_500_applied",
    "error_500_not_applied",
    "delayed_visibility",
    "wrong_amount",
    "reject_400",
    "unreadable",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
  transaction_id  TEXT PRIMARY KEY,
  shipment_ref    TEXT NOT NULL,
  case_id         TEXT,
  type            TEXT NOT NULL CHECK (type IN ('DEBIT','CREDIT')),
  amount_paise    INTEGER NOT NULL CHECK (amount_paise > 0),
  reason_code     TEXT NOT NULL,
  status          TEXT NOT NULL CHECK (status IN ('POSTED','PENDING','REVERSED')),
  idempotency_key TEXT UNIQUE,
  posted_at       TEXT NOT NULL
);
"""


@dataclass
class SimulatedLedger:
    """Implements Ledger."""

    path: Path
    failure_mode: str | None = None
    #: For `delayed_visibility`: how long a new transaction is invisible to read-back.
    visibility_delay_s: float = 30.0
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    #: Attempts to post, whatever their outcome (the kill-switch drill counts these).
    post_calls: int = 0
    idempotent_writes: bool = True
    lookup_supported: bool = True

    def __post_init__(self) -> None:
        if self.failure_mode is not None and self.failure_mode not in FAILURE_MODES:
            raise ValueError(f"unknown failure mode {self.failure_mode!r}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(_SCHEMA)

    # -- writing ---------------------------------------------------------------------------------

    def post_credit(self, req: CreditRequest, key: IdempotencyKey) -> TransactionRef:
        self.post_calls += 1  # every attempt, so a drill can prove there were none (I10)
        mode = self.failure_mode
        if mode == "timeout_before":
            # A one-off, like a real timeout: the next try reaches the ledger (S11).
            self.failure_mode = None
            raise TransientPortError("the ledger did not respond (nothing was written)")
        if mode == "error_500_not_applied":
            raise AmbiguousOutcome("the ledger returned 500 (it may or may not have written)")
        if mode == "reject_400":
            raise PermanentPortError("the ledger refused the credit", status=400)

        with self._write_lock() as db:
            earlier = db.execute(
                "SELECT transaction_id FROM entries WHERE idempotency_key = ?", (str(key),)
            ).fetchone()
            if earlier is not None:
                return TransactionRef(transaction_id=earlier[0], duplicate=True)

            paise = _paise(req.amount)
            if paise <= 0:
                raise PermanentPortError("the amount must be positive", status=400)
            if req.reason_code not in KNOWN_REASONS:
                raise PermanentPortError(f"unknown reason {req.reason_code!r}", status=400)
            clash = db.execute(
                "SELECT 1 FROM entries WHERE type='CREDIT' AND status='POSTED' "
                "AND shipment_ref=? AND reason_code=?",
                (req.shipment_ref, req.reason_code),
            ).fetchone()
            if clash is not None:
                raise PermanentPortError(
                    "a credit for this shipment and reason is already posted", status=409
                )
            if paise > self._outstanding(db, req.shipment_ref):
                raise PermanentPortError("the credit exceeds the outstanding charge", status=422)

            written = paise + 1 if mode == "wrong_amount" else paise
            txid = f"txn_{uuid.uuid4().hex[:20]}"
            db.execute(
                "INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    txid,
                    req.shipment_ref,
                    str(req.case_id),
                    "CREDIT",
                    written,
                    req.reason_code,
                    "POSTED",
                    str(key),
                    self.clock().isoformat(),
                ),
            )
        if mode in ("timeout_after", "error_500_applied"):
            raise AmbiguousOutcome("the ledger timed out after writing", external_ref=None)
        return TransactionRef(transaction_id=txid)

    # -- reading ---------------------------------------------------------------------------------

    def find_transaction(self, key: IdempotencyKey) -> TransactionRef | None:
        self._check_readable()
        with self._db() as db:
            row = db.execute(
                "SELECT transaction_id FROM entries WHERE idempotency_key = ?", (str(key),)
            ).fetchone()
        return TransactionRef(transaction_id=row[0]) if row else None

    def get_transaction(self, ref: TransactionRef) -> TransactionSnapshot:
        self._check_readable()
        with self._db() as db:
            row = db.execute(
                "SELECT transaction_id, shipment_ref, type, amount_paise, reason_code, status, "
                "idempotency_key, posted_at FROM entries WHERE transaction_id = ?",
                (ref.transaction_id,),
            ).fetchone()
        if row is None:
            raise ExternalNotFound(f"no transaction {ref.transaction_id}")
        posted_at = datetime.fromisoformat(row[7])
        if self.failure_mode == "delayed_visibility" and (
            (self.clock() - posted_at).total_seconds() < self.visibility_delay_s
        ):
            raise ExternalNotFound("the transaction is not visible yet")
        return TransactionSnapshot(
            transaction_id=row[0],
            shipment_ref=row[1],
            type=row[2],
            amount=Money.from_paise(row[3]),
            reason_code=row[4],
            status=row[5],
            idempotency_key=IdempotencyKey(row[6] or ""),
            posted_at=posted_at,
        )

    def get_entries(self, shipment_ref: str) -> Sequence[LedgerEntry]:
        self._check_readable()
        with self._db() as db:
            rows = db.execute(
                "SELECT transaction_id, shipment_ref, type, amount_paise, reason_code, posted_at "
                "FROM entries WHERE shipment_ref = ? AND status = 'POSTED' ORDER BY posted_at",
                (shipment_ref,),
            ).fetchall()
        return tuple(
            LedgerEntry(
                transaction_id=r[0],
                shipment_ref=r[1],
                type=r[2],
                amount=Money.from_paise(r[3]),
                reason_code=r[4],
                posted_at=datetime.fromisoformat(r[5]),
            )
            for r in rows
        )

    # -- test and seeding affordances ----------------------------------------------------------

    def seed_debit(
        self, shipment_ref: str, amount: Money, reason: str, transaction_id: str
    ) -> None:
        """A charge already on the shipment (the synthetic world's history). Not a port method."""
        with self._db() as db:
            db.execute(
                "INSERT OR IGNORE INTO entries VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    transaction_id,
                    shipment_ref,
                    None,
                    "DEBIT",
                    _paise(amount),
                    reason,
                    "POSTED",
                    None,
                    datetime(2026, 1, 1, tzinfo=UTC).isoformat(),
                ),
            )

    def credit_count(self, shipment_ref: str | None = None) -> int:
        """How many CREDIT entries exist - the duplicate-side-effect gate reads this."""
        with self._db() as db:
            if shipment_ref is None:
                row = db.execute("SELECT count(*) FROM entries WHERE type='CREDIT'").fetchone()
            else:
                row = db.execute(
                    "SELECT count(*) FROM entries WHERE type='CREDIT' AND shipment_ref=?",
                    (shipment_ref,),
                ).fetchone()
        return int(row[0])

    # -- internals -----------------------------------------------------------------------------

    def _check_readable(self) -> None:
        if self.failure_mode == "unreadable":
            raise ModuleUnavailable(MODULE)

    def _outstanding(self, db: sqlite3.Connection, shipment_ref: str) -> int:
        """Its own sum, from its own rows: debits minus posted credits, in paise."""
        debit = db.execute(
            "SELECT COALESCE(SUM(amount_paise),0) FROM entries "
            "WHERE shipment_ref=? AND type='DEBIT' AND status='POSTED'",
            (shipment_ref,),
        ).fetchone()[0]
        credit = db.execute(
            "SELECT COALESCE(SUM(amount_paise),0) FROM entries "
            "WHERE shipment_ref=? AND type='CREDIT' AND status='POSTED'",
            (shipment_ref,),
        ).fetchone()[0]
        return int(debit) - int(credit)

    def _db(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, isolation_level="IMMEDIATE")

    @contextmanager
    def _write_lock(self) -> Iterator[sqlite3.Connection]:
        """One explicit BEGIN IMMEDIATE around every read and the insert of a post.

        `isolation_level="IMMEDIATE"` alone is not enough: Python's sqlite3 only issues the
        BEGIN before the first INSERT, so the duplicate, clash and outstanding-charge reads
        ran outside the lock and two posts could both see room for one credit (Phase 5
        safety review, 8). Autocommit mode plus an explicit BEGIN takes the lock first.
        """
        db = sqlite3.connect(self.path, isolation_level=None)
        try:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")
        finally:
            db.close()


def _paise(amount: Money) -> int:
    """Its own conversion - deliberately not Money.to_paise - so the two sides stay independent."""
    value = Decimal(str(amount.amount)) * 100
    if value != value.to_integral_value():
        raise PermanentPortError("amounts have at most two decimals", status=400)
    return int(value)


__all__ = ["FAILURE_MODES", "KNOWN_REASONS", "MODULE", "SimulatedLedger"]
