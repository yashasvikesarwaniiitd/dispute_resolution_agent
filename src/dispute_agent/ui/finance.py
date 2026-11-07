"""Screen 4: a seller's ledger - every charge and credit, and where each credit came from.

Figures come only from the ledger (I9 for the screen: nothing here is computed except sums
of ledger rows). Each credit is traced back through the action that posted it to its case,
the person who approved it, and whether it was proved by reading it back (I2, I3).
A ledger row with no action behind it - a charge, or a credit posted outside the agent -
says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionStatus
from dispute_agent.ui.labels import words

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.external import LedgerEntry


@dataclass(frozen=True)
class Row:
    posted_at: datetime
    shipment_ref: str
    kind: str
    amount: Decimal
    reason: str
    transaction_id: str
    case_id: str = ""
    approved_by: str = ""
    proved: str = ""


@dataclass(frozen=True)
class Totals:
    charged: Decimal
    credited: Decimal

    @property
    def net(self) -> Decimal:
        return self.charged - self.credited


def rows(entries: Iterable[LedgerEntry], work: SqliteUnitOfWork | None) -> list[Row]:
    """Ledger rows newest first, each credit traced to its case where the agent posted it."""
    out = []
    for entry in entries:
        kind = str(getattr(entry.type, "value", entry.type)).upper()
        row = Row(
            posted_at=entry.posted_at,
            shipment_ref=entry.shipment_ref,
            kind="Credit" if kind == "CREDIT" else "Charge",
            amount=entry.amount.amount,
            reason=words(entry.reason_code or "weight_charge"),
            transaction_id=str(entry.transaction_id),
        )
        if kind == "CREDIT" and work is not None:
            action = work.actions.find_by_external_ref(str(entry.transaction_id))
            if action is None:
                row = replace(row, proved="Not posted by the agent")
            else:
                approval = work.approvals.get(action.approval_id) if action.approval_id else None
                who = (
                    approval.decided_by.id.removeprefix("human:")
                    if approval and approval.decided_by
                    else ""
                )
                proved = (
                    "Read back"
                    if action.status is ActionStatus.VERIFIED
                    else action.status.value.lower()
                )
                row = replace(row, case_id=str(action.case_id), approved_by=who, proved=proved)
        out.append(row)
    return sorted(out, key=lambda r: r.posted_at, reverse=True)


def totals(found: Iterable[Row]) -> Totals:
    charged = credited = Decimal(0)
    for row in found:
        if row.kind == "Credit":
            credited += row.amount
        else:
            charged += row.amount
    return Totals(charged=charged, credited=credited)


__all__ = ["Row", "Totals", "rows", "totals"]
