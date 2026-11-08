"""Screen 1: raise a ticket, as a seller. Layout over `ui.seller` (ADR 0044).

Composition (Hallmark redesign, full): the bill is the screen's object - the charged figure,
what the seller declared beside what the hub measured, and the one action - set on a card
beside the shipments to choose from. The shipments table is held short so the seller's
tickets come into view without scrolling.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.labels import when
from dispute_agent.ui.seller import catalogue, open_ticket_for, raise_ticket, sellers, tickets
from dispute_agent.ui.theme import chip, escape, pairs

#: About eight rows: enough to scan, short enough that the tickets stay in view.
SHIPMENTS_HEIGHT = 330


def page() -> None:
    settings = Settings()
    shipments = catalogue()
    seller_ref = sellers(shipments)[0]
    contact = (settings.test_seller_email or "").split(",")[0].strip() or None

    st.title("Raise a ticket")
    st.caption(
        f"Signed in as seller {seller_ref}. Every shipment here is synthetic; emails about "
        "your tickets go to the test seller address."
    )

    mine = []
    if data.has_store():
        with data.reader() as work:
            mine = tickets(work.cases.list_in_states(list(CaseState)), seller_ref)

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.header("Your shipments")
        st.dataframe(
            [
                {"Shipment": s.ref, "Status": s.status, "Zone": s.zone, "Charged": s.charged}
                for s in shipments
            ],
            hide_index=True,
            width="stretch",
            height=SHIPMENTS_HEIGHT,
        )
    with right, st.container(border=True, key="stock-bill"):
        st.header("Dispute a charge")
        refs = [s.ref for s in shipments]
        ref = st.selectbox("Shipment", refs, index=None, placeholder="Choose a shipment")
        chosen = next((s for s in shipments if s.ref == ref), None)
        if chosen is None:
            st.markdown(
                '<p class="muted">Choose a shipment to see its bill: what you were charged, '
                "what you declared and what the hub measured.</p>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                f'<p class="muted">Charged for zone {escape(chosen.zone)}</p>'
                f'<p class="figure">{escape(chosen.charged)}</p>'
                + pairs([("You declared", chosen.declared), ("The hub measured", chosen.measured)]),
                unsafe_allow_html=True,
            )
            existing = open_ticket_for(chosen, mine)
            if existing is not None:
                st.markdown(
                    f"You already have an open ticket on this shipment: "
                    f"{chip(existing.label, existing.tone)}",
                    unsafe_allow_html=True,
                )
            elif not data.has_store():
                st.markdown(f'<p class="muted">{escape(data.NO_STORE)}</p>', unsafe_allow_html=True)
            elif st.button("Raise a dispute", type="primary", width="stretch"):
                with st.spinner("Sending your ticket"):
                    # Hosted, the entry hands over the in-process service's route; locally it
                    # is the running API over loopback. Either way: POST /events (ADR 0045).
                    post = st.session_state.get("post") or data.poster(
                        settings.console_api_url, settings.http_timeout_s
                    )
                    raised = raise_ticket(
                        post,
                        chosen,
                        contact,
                        datetime.now(UTC),
                        f"console-{uuid.uuid4().hex}",
                    )
                # A line, not a boxed notice: a box inside this card would be a card in a card.
                css = "result" if raised.ok else "result result--error"
                st.markdown(
                    f'<p class="{css}">{escape(raised.message)}</p>', unsafe_allow_html=True
                )
                if raised.ok and raised.case_id:
                    st.session_state["case"] = raised.case_id

    st.header("Your tickets")
    if not data.has_store():
        st.info(data.NO_STORE)
    elif not mine:
        st.markdown(
            '<p class="muted">No tickets yet. Choose a shipment above and raise a dispute; '
            "it appears here with its status.</p>",
            unsafe_allow_html=True,
        )
    else:
        st.dataframe(
            [
                {
                    "Shipment": t.shipment_ref,
                    "Status": t.label,
                    "Opened": when(t.opened_at),
                    "Last update": when(t.updated_at),
                    "Case": t.case_id,
                }
                for t in mine
            ],
            hide_index=True,
            width="stretch",
        )
        pick, go = st.columns([3, 1], vertical_alignment="bottom")
        case = pick.selectbox(
            "See the agent's steps for",
            [t.case_id for t in mine],
            format_func=lambda cid: next(
                f"{t.shipment_ref}, {t.label}" for t in mine if t.case_id == cid
            ),
        )
        if go.button("Open the steps", width="stretch"):
            pages = st.session_state.get("pages")
            if pages:
                st.switch_page(pages["trail"], query_params={"case": case})
