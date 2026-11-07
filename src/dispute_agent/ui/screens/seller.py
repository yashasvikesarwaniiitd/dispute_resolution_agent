"""Screen 1: raise a ticket, as a seller. Layout over `ui.seller` (ADR 0044)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.seller import catalogue, open_ticket_for, raise_ticket, sellers, tickets
from dispute_agent.ui.theme import chip, escape


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
        st.subheader("Your shipments")
        st.dataframe(
            [
                {"Shipment": s.ref, "Status": s.status, "Zone": s.zone, "Charged": s.charged}
                for s in shipments
            ],
            hide_index=True,
            width="stretch",
        )
    with right:
        st.subheader("Dispute a charge")
        refs = [s.ref for s in shipments]
        ref = st.selectbox("Shipment", refs, index=None, placeholder="Choose a shipment")
        chosen = next((s for s in shipments if s.ref == ref), None)
        if chosen is None:
            st.markdown(
                '<p class="muted">Choose a shipment to see its bill.</p>', unsafe_allow_html=True
            )
        else:
            st.markdown(
                f"**Charged {escape(chosen.charged)}** for zone {escape(chosen.zone)}  \n"
                f"You declared {escape(chosen.declared)}  \n"
                f"The hub measured {escape(chosen.measured)}"
            )
            existing = open_ticket_for(chosen, mine)
            if existing is not None:
                st.markdown(
                    f"You already have an open ticket on this shipment: "
                    f"{chip(existing.label, existing.tone)}",
                    unsafe_allow_html=True,
                )
            elif not data.has_store():
                st.info(data.NO_STORE)
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
                (st.success if raised.ok else st.error)(raised.message)
                if raised.ok and raised.case_id:
                    st.session_state["case"] = raised.case_id

    st.subheader("Your tickets")
    if not data.has_store():
        st.info(data.NO_STORE)
    elif not mine:
        st.markdown(
            '<p class="muted">No tickets yet. Choose a shipment above to raise one.</p>',
            unsafe_allow_html=True,
        )
    else:
        st.dataframe(
            [
                {
                    "Case": t.case_id,
                    "Shipment": t.shipment_ref,
                    "Status": t.label,
                    "Opened": t.opened_at,
                    "Last update": t.updated_at,
                }
                for t in mine
            ],
            hide_index=True,
            width="stretch",
            column_config={
                "Opened": st.column_config.DatetimeColumn(format="D MMM, HH:mm"),
                "Last update": st.column_config.DatetimeColumn(format="D MMM, HH:mm"),
            },
        )
        case = st.selectbox(
            "See the agent's steps for",
            [t.case_id for t in mine],
            format_func=lambda cid: next(
                f"{t.shipment_ref}, {t.label}" for t in mine if t.case_id == cid
            ),
        )
        if st.button("Open the steps"):
            st.switch_page(st.session_state["pages"]["trail"], query_params={"case": case})
