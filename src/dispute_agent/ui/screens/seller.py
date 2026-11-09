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
from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data, picker
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
    shipments = catalogue()
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
    head, hint = st.columns([1, 1], vertical_alignment="bottom")
    head.header("Your shipments")
    hint.markdown('<p class="muted right">Click a row to see its bill.</p>', unsafe_allow_html=True)
    clicked = picker.show(
        {
            "label": "Your shipments",
            "selectable": True,
            "selected": st.session_state.get(SHIP),
            "grid": "1.2fr 1.4fr .6fr .9fr 1.5fr",
            "columns": [
                {"label": "Shipment"},
                {"label": "Status"},
                {"label": "Zone"},
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
                        {"kind": "num", "text": s.charged},
                        _ticket_cell(latest_ticket_for(s, mine)),
                    ],
                }
                for s in shipments
            ],
        },
        key="shipments",
    )
    if clicked["pick"] in refs and clicked["pick"] != st.session_state.get(SHIP):
        st.session_state[SHIP] = clicked["pick"]
        st.rerun()


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
