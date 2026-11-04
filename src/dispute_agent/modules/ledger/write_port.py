"""Presenting a `Ledger` as the executor's `WritePort` (P5-04, P5-05, money-rules.md section 8).

The executor knows send / look up / read back; the ledger speaks in credits. This joins them,
and in doing so states what a *correct* credit looks like when read back - the checklist in
money-rules.md section 8:

1. the transaction exists, type CREDIT, status POSTED;
2. the amount equals the approved amount exactly;
3. shipment and reason code equal the approved intent's;
4. the idempotency key equals the action's;
5. exactly one CREDIT for (shipment, reason) exists in the ledger.

Any difference is UNKNOWN `verification_mismatch` and a person decides (I4). The snapshot is
built from what the ledger returns, never from the receipt: a receipt is a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.external import CreditRequest, TransactionRef

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import CreditPayload
    from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
    from dispute_agent.kernel.ports import Ledger


@dataclass(frozen=True)
class CreditSnapshot:
    transaction_id: str
    type: str
    status: str
    amount_paise: int
    shipment_ref: str
    reason_code: str
    idempotency_key: IdempotencyKey
    credits_for_shipment_reason: int


@dataclass
class CreditWritePort:
    """Implements WritePort over any Ledger."""

    ledger: Ledger
    case_id: CaseId

    @property
    def idempotent_writes(self) -> bool:
        return self.ledger.idempotent_writes

    @property
    def lookup_supported(self) -> bool:
        return self.ledger.lookup_supported

    def send(self, payload: Any, key: IdempotencyKey) -> TransactionRef:
        approved: CreditPayload = payload
        return self.ledger.post_credit(
            CreditRequest(
                shipment_ref=approved.shipment_ref,
                amount=approved.amount,
                reason_code=approved.reason_code,
                case_id=self.case_id,
            ),
            key,
        )

    def find_sent(self, key: IdempotencyKey) -> TransactionRef | None:
        return self.ledger.find_transaction(key)

    def read_back(self, receipt: Any) -> CreditSnapshot:
        snapshot = self.ledger.get_transaction(receipt)
        same = [
            e
            for e in self.ledger.get_entries(snapshot.shipment_ref)
            if e.type == "CREDIT" and e.reason_code == snapshot.reason_code
        ]
        return CreditSnapshot(
            transaction_id=snapshot.transaction_id,
            type=snapshot.type,
            status=snapshot.status,
            amount_paise=snapshot.amount.to_paise(),
            shipment_ref=snapshot.shipment_ref,
            reason_code=snapshot.reason_code,
            idempotency_key=snapshot.idempotency_key,
            credits_for_shipment_reason=len(same),
        )

    def expected_snapshot(self, payload: Any, key: IdempotencyKey) -> dict[str, object]:
        approved: CreditPayload = payload
        return {
            "type": "CREDIT",
            "status": "POSTED",
            "amount_paise": approved.amount.to_paise(),
            "shipment_ref": approved.shipment_ref,
            "reason_code": approved.reason_code,
            "idempotency_key": key,
            "credits_for_shipment_reason": 1,
        }

    @staticmethod
    def receipt_from(external_ref: str) -> TransactionRef:
        return TransactionRef(transaction_id=external_ref)


__all__ = ["CreditSnapshot", "CreditWritePort"]
