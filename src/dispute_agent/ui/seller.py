"""Screen 1: a seller's shipments, their tickets, and raising a new one (ADR 0044).

The seller sees what they were billed, what the hub measured, and what the published rate
card gives (D73), worked out by the pack's calculator. Never the synthetic world's answer key
(the fixture's correct charge): only the agent's recalculation proves an error.

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
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import CARD, FIXTURES, fixture
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack
from dispute_agent.ui.labels import Tone, state

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import datetime
    from pathlib import Path

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


#: The seller page's examples: one for each way a dispute can go (the rest stay for tests).
EXAMPLES = (
    "kurta_box_larger",  # SHP-000001: the bill is right on the hub's measurement
    "calc_error_high_value",  # SHP-000002: billing error, credit for a reviewer to approve
    "ambiguous_image",  # SHP-000003: evidence is unclear, a person decides
    "scanner_fault",  # SHP-000005: the agent asks the hub (Jira) to re-measure
    "not_delivered",  # SHP-000007: returned to origin, not eligible
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
    #: What the rate card says, worked out by the pack's own calculator (ADR 0050): on the
    #: hub's measurement (what the charge should be) and on the seller's declared size.
    card_on_measured: str = ""
    card_on_declared: str = ""
    #: The card on the declared size, as an amount alone, for the shipments table: what the
    #: seller would expect to pay, beside what they were charged. Worked out for display only;
    #: the ledger keeps one charge per shipment (a second debit would read as a duplicate).
    on_declared: str = ""
    #: Charged minus the card on the hub's measurement, when the bill is higher; else "".
    over_card: str = ""
    #: The rate card worked out step by step, as (step, on declared, on hub's measurement),
    #: so the seller sees exactly how each figure was reached before disputing it.
    breakdown: tuple[tuple[str, str, str], ...] = ()
    #: One plain sentence: does the bill match the hub's measurement, and what to dispute.
    verdict: str = ""


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


def _card(pack: WeightDisputePack, dims: Any, grams: int, zone: str, packaging: str) -> Any:
    return pack.charge_for(dims.length_cm, dims.width_cm, dims.height_cm, grams, zone, packaging)


def _card_line(charge: Any) -> str:
    kg = Decimal(charge.chargeable_grams) / 1000
    return f"{render_fact(charge.amount)} for {kg:.2f} kg"


def catalogue(added: Path | None = None, examples: Iterable[str] = FIXTURES) -> list[Shipment]:
    """Every synthetic shipment once, served as the running app serves it: first fixture wins.

    `added` is the store of shipments added on the console (ADR 0056); they follow the
    fixtures, and a fixture keeps its reference.
    """
    from dispute_agent.modules.evidence.synthetic_shipments.added import AddedShipments

    pack = WeightDisputePack()  # the card the running app uses (app.py builds the same)
    seen: dict[str, Shipment] = {}
    built = [fixture(name) for name in examples]
    if added is not None:
        built += AddedShipments(added).all()
    for one in built:
        s = one.shipment
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
            **_cards(pack, s),
        )
    return sorted(seen.values(), key=lambda s: s.ref)


def _kg(grams: int) -> str:
    """Grams as kg, to the gram when it matters: 1,100 -> 1.10, 2,244 -> 2.244."""
    text = f"{Decimal(grams) / 1000:.3f}"
    return f"{text[:-1] if text.endswith('0') else text} kg"


TIMES, DIVIDE = chr(0xD7), chr(0xF7)  # the multiplication and division signs


def _money(paise: int) -> str:
    return render_fact(Money.from_paise(paise))


def _dims(d: Any) -> str:
    return f"{d.length_cm} {TIMES} {d.width_cm} {TIMES} {d.height_cm} cm"


def breakdown(
    declared: tuple[Any, Any], measured: tuple[Any, Any]
) -> tuple[tuple[str, str, str], ...]:
    """Each step of the rate card, on (dims, charge) as declared and as the hub measured."""
    (d_dims, d), (m_dims, m) = declared, measured
    by_size = all(c.chargeable_grams == max(c.dead_grams, c.volumetric_grams) for c in (d, m))
    return (
        (f"Size (L {TIMES} W {TIMES} H)", _dims(d_dims), _dims(m_dims)),
        ("Scale weight", _kg(d.dead_grams), _kg(m.dead_grams)),
        (
            f"Size weight (L {TIMES} W {TIMES} H {DIVIDE} {CARD.volumetric_divisor:,})",
            _kg(d.volumetric_grams),
            _kg(m.volumetric_grams),
        ),
        (
            "Charged weight (the higher)" if by_size else "Charged weight",
            _kg(d.chargeable_grams),
            _kg(m.chargeable_grams),
        ),
        (f"Slabs of {_kg(CARD.slab_grams)} (rounded up)", str(d.slabs), str(m.slabs)),
        (
            f"Rate per slab (zone {m.zone})",
            _money(d.rate_paise_per_slab),
            _money(m.rate_paise_per_slab),
        ),
        (f"Charge (slabs {TIMES} rate)", _money(d.amount_paise), _money(m.amount_paise)),
    )


def verdict(billed: int, on_declared: int, on_measured: int) -> str:
    """What the bill means for the seller, in one sentence, from the three amounts."""
    if billed > on_measured:
        return (
            f"You were charged {_money(billed - on_measured)} more than the rate card gives for "
            "the hub's own measurement. That is a billing error you can dispute."
        )
    if billed > on_declared:
        return (
            "Your bill matches the hub's measurement. It is higher than your declared parcel "
            "because the hub measured it bigger or heavier. Dispute it if you think the hub "
            "measured wrong: the agent checks the hub's figures."
        )
    return "Your bill matches the parcel you declared. There is nothing to dispute."


def _cards(pack: WeightDisputePack, s: Any) -> dict[str, Any]:
    on_measured = _card(pack, s.measured, s.measured_dead_grams, s.zone, s.packaging)
    on_declared = _card(pack, s.declared, s.declared_dead_grams, s.zone, s.packaging)
    over = s.billed_paise - on_measured.amount_paise
    return {
        "card_on_measured": _card_line(on_measured),
        "card_on_declared": _card_line(on_declared),
        "on_declared": render_fact(on_declared.amount),
        "over_card": render_fact(Money.from_paise(over)) if over > 0 else "",
        "breakdown": breakdown((s.declared, on_declared), (s.measured, on_measured)),
        "verdict": verdict(s.billed_paise, on_declared.amount_paise, on_measured.amount_paise),
    }


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


def latest_ticket_for(shipment: Shipment, mine: Iterable[Ticket]) -> Ticket | None:
    """The ticket to show beside a shipment: the open one, else the latest closed one.

    Only the open one blocks a new ticket (`open_ticket_for`); a resolved one still belongs
    in the table, so the seller sees how it ended.
    """
    on_it = [t for t in mine if t.shipment_ref == shipment.ref]
    open_one = next((t for t in on_it if t.is_open), None)
    return open_one or max(on_it, key=lambda t: t.updated_at, default=None)


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
