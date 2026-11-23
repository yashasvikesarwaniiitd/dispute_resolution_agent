"""Adding a shipment from the console (ADR 0056): the preview and the save, without Streamlit.

The preview is code's arithmetic on what the form holds, shown before anything is saved, so a
person knows which case they are making ("overcharged by ₹38"). It states what code finds,
never what the agent will decide: that is the agent's to work out.

Saving checks the photo first, then stores the shipment, then posts its charge to the ledger
the way start-up seeds the built-in shipments. A refused photo stores nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.modules.evidence.synthetic_shipments.added import (
    AddedShipments,
    NewShipment,
    as_fixture,
    correct_paise,
    photo_kind,
)
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import CARD
from dispute_agent.packs.weight_dispute.calculator import calculate

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

#: Status codes as the form offers them, in the screens' words.
STATUS_CHOICES = {
    "Delivered": "DELIVERED",
    "Returned to origin": "RTO",
    "Lost": "LOST",
    "Cancelled": "CANCELLED",
}


def rupees(paise: int) -> str:
    return render_fact(Money.from_paise(paise))


@dataclass(frozen=True)
class Preview:
    #: The rate card on the seller's declared size: what they expect to pay.
    expected: str
    #: The rate card on the hub's measurement: what a correct bill is.
    correct: str
    #: What will be debited (each time, when charged twice).
    charged: str
    total: str
    #: What code finds, in one sentence.
    finding: str
    error: bool


def preview(new: NewShipment) -> Preview:
    expected = calculate(new.declared, new.declared_grams, new.zone, CARD, new.packaging)
    correct = correct_paise(new)
    charged = new.charged_paise if new.charged_paise is not None else correct
    debits = 2 if new.charged_twice else 1
    if new.status != "DELIVERED":
        finding, error = (
            "Not delivered: a dispute on it closes as not eligible, whatever the bill.",
            False,
        )
    elif new.charged_twice and charged != correct:
        finding, error = (
            "Two errors (a wrong charge, debited twice): code cannot pick one amount to credit.",
            True,
        )
    elif new.charged_twice:
        finding, error = (f"One error: charged twice. The second {rupees(charged)} is extra.", True)
    elif charged > correct:
        over = rupees(charged - correct)
        finding, error = (f"One error: {over} more than the rate card gives.", True)
    elif charged < correct:
        finding, error = ("Charged less than the rate card gives: nothing to credit.", False)
    else:
        finding, error = (
            "No error: the bill matches the rate card for the hub's measurement.",
            False,
        )
    return Preview(
        expected=rupees(expected.amount_paise),
        correct=rupees(correct),
        charged=rupees(charged) + (" twice" if debits == 2 else ""),
        total=rupees(charged * debits),
        finding=finding,
        error=error,
    )


def save(
    store: AddedShipments,
    ledger: Path,
    new: NewShipment,
    photo: bytes | None,
    now: datetime,
) -> str:
    """Store the shipment, its photo and its charge. Returns its reference.

    Raises ShipmentRejectedError, before anything is stored, for a bad value or photo.
    """
    from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger

    if photo is not None:
        photo_kind(photo)  # refuse a bad photo before the shipment is stored
    built = store.add(new, now.isoformat())
    if photo is not None:
        store.save_photo(new.ref, photo)
    books = SimulatedLedger(ledger)
    for entry in as_fixture(new).shipment.ledger:
        books.seed_debit(
            entry.shipment_ref,
            Money.from_paise(entry.amount_paise),
            entry.reason_code or "weight_charge",
            entry.transaction_id,
        )
    return built.shipment.ref


__all__ = ["STATUS_CHOICES", "Preview", "preview", "rupees", "save"]
