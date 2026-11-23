"""Screen 1: raise a ticket, as a seller (design handoff 4, Screen 1; ADR 0044, ADR 0048).

The shipments table and the bill card share one selection: a clicked row and the list in the
bill always agree. The bill shows what the seller was charged, what they declared beside what
the hub measured, and the one action. A shipment that was not delivered says so before a
ticket is raised on it. Each ticket says in a sentence what is happening on it and links to
the agent's steps.

The seller never sees the correct charge, and the dispute carries no free text (brief 13.5).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.modules.evidence.synthetic_shipments.added import (
    AddedShipments,
    NewShipment,
    ShipmentRejectedError,
    check,
    dimensions,
)
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import CARD
from dispute_agent.ui import data, picker
from dispute_agent.ui.add_shipment import STATUS_CHOICES, preview, save
from dispute_agent.ui.journey import journey
from dispute_agent.ui.labels import when
from dispute_agent.ui.photos import photo_for
from dispute_agent.ui.seller import (
    catalogue,
    latest_ticket_for,
    open_ticket_for,
    raise_ticket,
    sellers,
    tickets,
)
from dispute_agent.ui.theme import chip, escape, line, pairs

if TYPE_CHECKING:
    from dispute_agent.ui.seller import Raised, Shipment, Ticket

SHIP = "ship"  # the one selection the table and the bill share
FILTERS = ("Open", "Closed", "All")


def page() -> None:
    settings = Settings()
    shipments = catalogue(data.SHIPMENTS)
    seller_ref = sellers(shipments)[0]
    contact = (settings.test_seller_email or "").split(",")[0].strip() or None

    st.title("Raise a ticket")
    st.caption(
        f"Signed in as seller {seller_ref}. Every shipment here is synthetic; emails about your "
        "tickets go to the test seller address."
    )
    mine: list[Ticket] = []
    happening: dict[str, str] = {}
    if data.has_store():
        with data.reader() as work:
            cases = work.cases.list_in_states(list(CaseState))
            mine = tickets(cases, seller_ref)
            ours = {t.case_id for t in mine}
            happening = {str(c.id): journey(work, c).headline for c in cases if str(c.id) in ours}

    refs = [s.ref for s in shipments]
    st.session_state.setdefault(SHIP, None)
    left, right = st.columns([3, 2], gap="large")
    with left, st.container(key="card-shipments"):
        _shipments(shipments, mine, refs)
    with right, st.container(key="card-bill"):
        _bill(shipments, mine, refs, settings, contact)
    with st.container(key="card-tickets"):
        _tickets(mine, happening)


def _shipments(shipments: list[Shipment], mine: list[Ticket], refs: list[str]) -> None:
    head, add = st.columns([2, 1], vertical_alignment="center")
    head.header("Your shipments")
    if add.button("Add a shipment", icon=":material/add:", key="open-add", width="stretch"):
        _add_dialog()
    added = st.session_state.pop("added", None)
    if added:
        st.markdown(
            line(f"Added {added}. It is chosen on the right: raise a dispute on it when ready."),
            unsafe_allow_html=True,
        )
    clicked = picker.show(
        {
            "label": "Your shipments",
            "selectable": True,
            "selected": st.session_state.get(SHIP),
            # Status wide enough for "Returned to origin" on one line.
            "grid": "1.2fr 1.6fr .45fr 1fr 1fr 1.7fr",
            "columns": [
                {"label": "Shipment"},
                {"label": "Status"},
                {"label": "Zone"},
                {"label": "Expected", "align": "right"},
                {"label": "Charged", "align": "right"},
                {"label": "Your ticket"},
            ],
            "rows": [
                {
                    "id": s.ref,
                    "cells": [
                        {"kind": "num", "text": s.ref},
                        {"kind": "text" if s.status == "Delivered" else "error", "text": s.status},
                        s.zone,
                        {"kind": "num", "text": s.on_declared},
                        {"kind": "num", "text": s.charged},
                        _ticket_cell(latest_ticket_for(s, mine)),
                    ],
                }
                for s in shipments
            ],
        },
        key="shipments",
    )
    st.markdown(
        '<p class="muted">Click a row to see its bill. Expected: what the rate card gives for '
        "the size and weight you declared. Charged: what you were billed after the hub weighed "
        "the parcel.</p>",
        unsafe_allow_html=True,
    )
    if clicked["pick"] in refs and clicked["pick"] != st.session_state.get(SHIP):
        st.session_state[SHIP] = clicked["pick"]
        st.rerun()


#: The form's widget keys, cleared after a save so the next form starts fresh.
ADD = "add-"


@st.dialog("Add a shipment", width="large")
def _add_dialog() -> None:
    """A synthetic shipment: sizes, weights and a zone; code works out the charge (ADR 0056)."""
    store = AddedShipments(data.SHIPMENTS)
    st.markdown(
        '<p class="muted">Synthetic data only. The rate card works out the charge from the '
        "hub's measurement; tick a billing mistake below to try one. The photo is optional.</p>",
        unsafe_allow_html=True,
    )
    top = st.columns(3)
    ref = top[0].text_input("Shipment ID", value=store.next_ref(), key=f"{ADD}ref").strip()
    zones = list(CARD.zone_names())
    zone = top[1].selectbox("Delivery zone", zones, index=min(1, len(zones) - 1), key=f"{ADD}zone")
    status = top[2].selectbox("Status", list(STATUS_CHOICES), key=f"{ADD}status")
    declared = _size_inputs("Seller declared", "declared", (20.0, 15.0, 10.0), 480)
    measured = _size_inputs("Hub measured", "measured", (30.0, 22.0, 15.0), 1100)
    with st.expander("Billing mistakes to try (optional)"):
        other = st.checkbox("Charge a different amount", key=f"{ADD}override")
        amount = st.number_input(
            "Amount charged (₹)",
            min_value=1.0,
            max_value=100000.0,
            value=200.0,
            step=1.0,
            disabled=not other,
            key=f"{ADD}amount",
        )
        twice = st.checkbox("Charged twice (the same charge debited again)", key=f"{ADD}twice")
    note = st.text_input(
        "Hub's photo note (optional)",
        max_chars=300,
        placeholder="For example: possibly two parcels strapped together",
        key=f"{ADD}note",
    ).strip()
    photo = st.file_uploader(
        "Parcel photo (optional, JPG or PNG, up to 5 MB)",
        type=["jpg", "jpeg", "png"],
        key=f"{ADD}photo",
    )

    new: NewShipment | None = None
    try:
        new = NewShipment(
            ref=ref,
            zone=str(zone),
            status=STATUS_CHOICES[str(status)],
            declared=dimensions(*declared[0]),
            declared_grams=declared[1],
            measured=dimensions(*measured[0]),
            measured_grams=measured[1],
            charged_paise=int((Decimal(str(amount)) * 100).quantize(Decimal(1))) if other else None,
            charged_twice=twice,
            photo_note=note or None,
        )
        check(new)
    except ShipmentRejectedError as bad:
        new = None
        st.markdown(line(str(bad), error=True), unsafe_allow_html=True)
    if new is not None:
        seen = preview(new)
        st.markdown(
            '<hr class="rule"><p class="muted">What code works out, before anything is saved</p>'
            + pairs(
                [
                    ("Seller expects (declared size)", seen.expected),
                    ("Correct charge (hub's measurement)", seen.correct),
                    ("Will be charged", seen.charged),
                ]
            )
            + line(seen.finding, error=seen.error),
            unsafe_allow_html=True,
        )
    if st.button("Add shipment", type="primary", disabled=new is None, key=f"{ADD}save"):
        assert new is not None
        try:
            saved = save(
                store,
                data.LEDGER,
                new,
                photo.getvalue() if photo is not None else None,
                datetime.now(UTC),
            )
        except ShipmentRejectedError as bad:
            st.markdown(line(str(bad), error=True), unsafe_allow_html=True)
            return
        for key in [k for k in st.session_state if str(k).startswith(ADD)]:
            del st.session_state[key]
        st.session_state[SHIP] = saved
        st.session_state["added"] = saved
        st.rerun()


def _size_inputs(
    title: str, key: str, sides: tuple[float, float, float], grams: int
) -> tuple[tuple[float, float, float], int]:
    """Length, width and height in cm and a weight in grams, on one row."""
    st.markdown(f'<p class="caption">{escape(title)}</p>', unsafe_allow_html=True)
    cols = st.columns(4)
    values = [
        cols[i].number_input(
            f"{name} (cm)",
            min_value=0.1,
            max_value=300.0,
            value=default,
            step=0.5,
            format="%.1f",
            key=f"{ADD}{key}-{name.lower()}",
        )
        for i, (name, default) in enumerate(zip(("Length", "Width", "Height"), sides, strict=True))
    ]
    weight = cols[3].number_input(
        "Weight (g)",
        min_value=1,
        max_value=100_000,
        value=grams,
        step=10,
        key=f"{ADD}{key}-grams",
    )
    return (values[0], values[1], values[2]), int(weight)


def _ticket_cell(ticket: Ticket | None) -> object:
    return {"chip": ticket.label, "tone": ticket.tone.value} if ticket else ""


def _over(chosen: Shipment) -> str:
    """Say plainly when the bill is above the card on the hub's own measurement."""
    if not chosen.over_card:
        return ""
    return (
        f'<p class="line line--error" role="alert">You were charged {escape(chosen.over_card)} '
        "more than the rate card gives for the hub's own measurement.</p>"
    )


def _bill(
    shipments: list[Shipment],
    mine: list[Ticket],
    refs: list[str],
    settings: Settings,
    contact: str | None,
) -> None:
    st.header("Dispute a charge")
    ref = st.selectbox("Shipment", refs, placeholder="Choose a shipment", key=SHIP)
    chosen = next((s for s in shipments if s.ref == ref), None)
    if chosen is None:
        st.markdown(
            '<p class="muted">Choose a shipment to see its bill: what you were charged, what you '
            "declared and what the hub measured.</p>",
            unsafe_allow_html=True,
        )
        return
    st.markdown(
        f'<p class="muted">Charged for zone {escape(chosen.zone)}</p>'
        f'<p class="figure-lg">{escape(chosen.charged)}</p><hr class="rule">'
        + pairs([("You declared", chosen.declared), ("The hub measured", chosen.measured)])
        + '<hr class="rule"><p class="muted">What the rate card says, worked out in code</p>'
        + pairs(
            [
                ("On the hub's measurement", chosen.card_on_measured),
                ("On your declared size", chosen.card_on_declared),
            ]
        )
        + _over(chosen)
        + '<hr class="rule">',
        unsafe_allow_html=True,
    )
    photo = photo_for(chosen.ref)
    if photo is not None:
        st.image(str(photo), caption="Your parcel photo", width="stretch")
    if chosen.status != "Delivered":
        st.markdown(
            f'<p class="line line--error" role="alert">⚠ Its status is "{escape(chosen.status)}", '
            "not delivered. Only delivered shipments can be disputed, so a ticket on it would close "
            "as not eligible.</p>",
            unsafe_allow_html=True,
        )
    _result(st.session_state.pop("raised", None), chosen.ref)
    existing = open_ticket_for(chosen, mine)
    if existing is not None:
        st.markdown(
            "<p>You already have an open ticket on this shipment: "
            f"{chip(existing.label, existing.tone)}</p>",
            unsafe_allow_html=True,
        )
        _steps_link(existing.case_id, "See what the agent has done on it")
    elif not data.has_store():
        st.markdown(f'<p class="muted">{escape(data.NO_STORE)}</p>', unsafe_allow_html=True)
    elif st.button("Raise a dispute", type="primary", width="stretch"):
        with st.spinner("Sending your ticket"):
            # Hosted, the entry hands over the in-process service's route; locally it is the
            # running API over loopback. Either way: POST /events (ADR 0045).
            post = st.session_state.get("post") or data.poster(
                settings.console_api_url, settings.http_timeout_s
            )
            raised = raise_ticket(
                post, chosen, contact, datetime.now(UTC), f"console-{uuid.uuid4().hex}"
            )
        if raised.ok and raised.case_id:
            st.session_state["case"] = raised.case_id
            st.session_state["just_raised"] = raised.case_id
        # Said after a rerun, so the new ticket is already in Your tickets below.
        st.session_state["raised"] = (chosen.ref, raised)
        st.rerun()
    st.markdown(
        '<p class="muted">The agent checks the charge in code and a person approves any credit. '
        "You will get an email at the test seller address.</p>",
        unsafe_allow_html=True,
    )


def _result(last: tuple[str, Raised] | None, ref: str) -> None:
    """What happened when this shipment's ticket was raised: one line, not a boxed alert."""
    if last is None or last[0] != ref:
        return
    raised = last[1]
    st.markdown(line(raised.message, error=not raised.ok), unsafe_allow_html=True)
    if raised.ok and raised.case_id:
        _steps_link(raised.case_id, "Watch the agent work on it")


def _steps_link(case_id: str, label: str) -> None:
    pages = st.session_state.get("pages")
    if pages:  # absent only when a screen runs on its own, as in tests
        st.page_link(pages["trail"], label=f"{label} →", query_params={"case": case_id})


def _tickets(mine: list[Ticket], happening: dict[str, str]) -> None:
    open_n = sum(1 for t in mine if t.is_open)
    counts = {"Open": open_n, "Closed": len(mine) - open_n, "All": len(mine)}
    head, pick = st.columns([1, 1], vertical_alignment="bottom")
    head.header("Your tickets")
    with pick:
        chosen = (
            st.segmented_control(
                "Show",
                FILTERS,
                default="All",
                format_func=lambda f: f"{f} ({counts[f]})",
                key="ticket-filter",
                label_visibility="collapsed",
            )
            or "All"
        )
    if not data.has_store():
        st.markdown(
            '<p class="muted">There is no case store yet. It is created when the dispute service '
            "starts.</p>",
            unsafe_allow_html=True,
        )
        return
    if not mine:
        st.markdown(
            '<p class="muted">No tickets yet. Choose a shipment above and raise a dispute; it '
            "appears here with its status.</p>",
            unsafe_allow_html=True,
        )
        return
    shown = [t for t in mine if chosen == "All" or (t.is_open == (chosen == "Open"))]
    fresh = st.session_state.get("just_raised")
    clicked = picker.show(
        {
            "label": "Your tickets",
            "grid": "1fr 2.6fr 1.3fr 1.3fr 1.2fr",
            "empty": "No tickets in this view.",
            "columns": [
                {"label": "Shipment"},
                {"label": "What is happening"},
                {"label": "Opened"},
                {"label": "Last update"},
                {"label": ""},
            ],
            "rows": [
                {
                    "id": t.case_id,
                    "tint": t.case_id == fresh,
                    "cells": [
                        {"kind": "num", "text": t.shipment_ref},
                        {
                            "stack": [
                                {"chip": t.label, "tone": t.tone.value},
                                {"kind": "text", "text": happening.get(t.case_id, "")},
                            ]
                        },
                        {"kind": "muted", "text": when(t.opened_at)},
                        {"kind": "muted", "text": when(t.updated_at)},
                        {
                            "link": "Open the steps →",
                            "target": t.case_id,
                            "label": f"Open the agent's steps for {t.shipment_ref}",
                        },
                    ],
                }
                for t in shown
            ],
        },
        key="tickets",
    )
    pages = st.session_state.get("pages")
    if clicked["open"] and pages:
        st.switch_page(pages["trail"], query_params={"case": clicked["open"]})
