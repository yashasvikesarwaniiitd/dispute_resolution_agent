"""Screen 1: a seller's shipments, their tickets, and raising a new one (ADR 0044).

The seller sees what they were billed and what the hub measured - never the synthetic
world's answer key (`correct_paise`), which only the recalculation may arrive at.

Raising a ticket posts the same body the dispute form posts to `POST /events`, so it takes
the real route: intake, the allowlist (I11), dedupe and the runner. The console never opens
a case itself. The dispute carries no free text, as the API's form does not: a seller's
words are untrusted input to the investigator, and adding a box is a separate decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FIXTURES, fixture
from dispute_agent.ui.labels import Tone, state

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime

    from dispute_agent.kernel.domain.models import Case

#: Closed states: a new ticket for the same shipment is a new dispute, not a duplicate.
CLOSED = frozenset(
    {
        CaseState.RESOLVED_CREDITED,
        CaseState.RESOLVED_UPHELD,
        CaseState.WITHDRAWN,
        CaseState.CLOSED_NO_RESPONSE,
        CaseState.REJECTED_INELIGIBLE,
        CaseState.HANDED_TO_HUMAN,
    }
)


#: Shipment statuses as a seller reads them; RTO is the courier term for "returned".
STATUS = {
    "DELIVERED": "Delivered",
    "RTO": "Returned to origin",
    "LOST": "Lost",
    "CANCELLED": "Cancelled",
}


@dataclass(frozen=True)
class Shipment:
    ref: str
    claim_ref: str
    seller_ref: str
    status: str
    zone: str
    declared: str
    measured: str
    charged: str


@dataclass(frozen=True)
class Ticket:
    case_id: str
    shipment_ref: str
    claim_ref: str
    label: str
    tone: Tone
    opened_at: datetime
    updated_at: datetime
    review_reason: str | None
    is_open: bool


@dataclass(frozen=True)
class Raised:
    """What happened when a ticket was raised: a case, a refusal, or no API to reach."""

    ok: bool
    message: str
    case_id: str | None = None


def _size(dims: Any, grams: int) -> str:
    return (
        f"{dims.length_cm} x {dims.width_cm} x {dims.height_cm} cm, {Decimal(grams) / 1000:.2f} kg"
    )


def catalogue() -> list[Shipment]:
    """Every synthetic shipment once, served as the running app serves it: first fixture wins."""
    seen: dict[str, Shipment] = {}
    for name in FIXTURES:
        s = fixture(name).shipment
        if s.ref in seen:
            continue
        seen[s.ref] = Shipment(
            ref=s.ref,
            claim_ref=s.claim_ref,
            seller_ref=s.seller_id,
            status=STATUS.get(s.status, s.status.replace("_", " ").capitalize()),
            zone=s.zone,
            declared=_size(s.declared, s.declared_dead_grams),
            measured=_size(s.measured, s.measured_dead_grams),
            charged=render_fact(Money.from_paise(s.billed_paise)),
        )
    return sorted(seen.values(), key=lambda s: s.ref)


def sellers(shipments: Iterable[Shipment]) -> list[str]:
    return sorted({s.seller_ref for s in shipments})


def ticket(case: Case) -> Ticket:
    label, tone = state(case.state)
    reason = case.review_reason
    return Ticket(
        case_id=str(case.id),
        shipment_ref=case.facts.shipment_ref,
        claim_ref=case.facts.claim_ref,
        label=label,
        tone=tone,
        opened_at=case.opened_at,
        updated_at=case.updated_at,
        review_reason=None if reason is None else str(reason.code.value),
        is_open=case.state not in CLOSED,
    )


def tickets(cases: Iterable[Case], seller_ref: str | None = None) -> list[Ticket]:
    """A seller's tickets (or everyone's), newest first."""
    chosen = [c for c in cases if seller_ref is None or c.facts.seller_ref == seller_ref]
    return [ticket(c) for c in sorted(chosen, key=lambda c: c.opened_at, reverse=True)]


def open_ticket_for(shipment: Shipment, mine: Iterable[Ticket]) -> Ticket | None:
    return next(
        (t for t in mine if t.shipment_ref == shipment.ref and t.is_open),
        None,
    )


def dispute_body(shipment: Shipment, contact: str, now: datetime, event_id: str) -> dict[str, str]:
    """Exactly what `POST /events` accepts (`api/events.py`, RaiseDispute)."""
    return {
        # The dispute form's own source: the console is another face of the same form.
        "source": "dispute_api",
        "source_event_id": event_id,
        "shipment_ref": shipment.ref,
        "claim_ref": shipment.claim_ref,
        "seller_ref": shipment.seller_ref,
        "seller_contact": contact,
        "raised_at": now.isoformat(),
    }


def raise_ticket(
    post: Callable[[dict[str, str]], tuple[int, dict[str, Any]]],
    shipment: Shipment,
    contact: str | None,
    now: datetime,
    event_id: str,
) -> Raised:
    """Post the dispute and say what happened, in words a seller understands."""
    if not contact:
        return Raised(False, "No test seller address is set (TEST_SELLER_EMAIL), so no ticket.")
    try:
        status, answer = post(dispute_body(shipment, contact, now, event_id))
    except OSError:
        return Raised(
            False,
            "The dispute service is not running, so the ticket was not raised. Try again in a minute.",
        )
    if status == 422:
        return Raised(
            False,
            "This seller address is not on the test allowlist. Use the test seller address and try again.",
        )
    if status != 200:
        return Raised(
            False, f"The dispute service refused the ticket (HTTP {status}). Nothing was raised."
        )
    case_id = answer.get("case_id")
    if answer.get("duplicate"):
        return Raised(True, "This ticket was already raised; showing the existing case.", case_id)
    return Raised(True, "Ticket raised. The agent has started on it.", case_id)


__all__ = [
    "CLOSED",
    "Raised",
    "Shipment",
    "Ticket",
    "catalogue",
    "dispute_body",
    "open_ticket_for",
    "raise_ticket",
    "sellers",
    "ticket",
    "tickets",
]
