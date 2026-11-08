"""Screen 1: raise a ticket, as a seller. Layout over `ui.seller` (ADR 0044).

Composition (Hallmark redesign, full; UX pass): the bill is the screen's object - the charged
figure, what the seller declared beside what the hub measured, and the one action - set on a
card beside the shipments to choose from. A shipment is chosen by clicking its row or from
the list; a shipment that was not delivered says so before a ticket is raised on it. Each
ticket says in a sentence what is happening on it, and links to the agent's steps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.journey import journey
from dispute_agent.ui.labels import when
from dispute_agent.ui.seller import catalogue, open_ticket_for, raise_ticket, sellers, tickets
from dispute_agent.ui.theme import chip, escape, pairs

if TYPE_CHECKING:
    from dispute_agent.ui.seller import Raised

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
    happening: dict[str, str] = {}
    if data.has_store():
        with data.reader() as work:
            cases = work.cases.list_in_states(list(CaseState))
            mine = tickets(cases, seller_ref)
            ours = {t.case_id for t in mine}
            happening = {str(c.id): journey(work, c).headline for c in cases if str(c.id) in ours}

    refs = [s.ref for s in shipments]
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.header("Your shipments")
        picked = st.dataframe(
            [
                {
                    "Shipment": s.ref,
                    "Status": s.status,
                    "Zone": s.zone,
                    "Charged": s.charged,
                    "Your ticket": (open_ticket_for(s, mine) or _NONE).label,
                }
                for s in shipments
            ],
            hide_index=True,
            width="stretch",
            height=SHIPMENTS_HEIGHT,
            on_select="rerun",
            selection_mode="single-row",
            # A new key clears the row tick when the shipment is changed from the list.
            key=f"shipments-{st.session_state.get('_rows', 0)}",
        )
        _follow(picked, refs)
        st.markdown('<p class="muted">Click a row to see its bill.</p>', unsafe_allow_html=True)
    with right, st.container(border=True, key="stock-bill"):
        st.header("Dispute a charge")
        # Starts empty; a clicked row (or the list) sets it. Set through the key, so the row
        # click and the list never fight over a default.
        st.session_state.setdefault("ship", None)
        ref = st.selectbox(
            "Shipment", refs, placeholder="Choose a shipment", key="ship", on_change=_listed
        )
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
            if chosen.status != "Delivered":
                st.markdown(
                    f'<p class="result result--error">Its status is "{escape(chosen.status)}", '
                    "not delivered. Only delivered shipments can be disputed, so a ticket on it "
                    "would close as not eligible.</p>",
                    unsafe_allow_html=True,
                )
            _result(st.session_state.pop("raised", None), chosen.ref)
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
                if raised.ok and raised.case_id:
                    st.session_state["case"] = raised.case_id
                # Shown after a rerun, so the new ticket is already in the table below.
                st.session_state["raised"] = (chosen.ref, raised)
                st.rerun()

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
                    "What is happening": happening.get(t.case_id, ""),
                    "Opened": when(t.opened_at),
                    "Last update": when(t.updated_at),
                }
                for t in mine
            ],
            hide_index=True,
            width="stretch",
            column_config={"What is happening": st.column_config.TextColumn(width="large")},
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


class _NoTicket:
    label = ""


_NONE = _NoTicket()


def _follow(picked: object, refs: list[str]) -> None:
    """A clicked shipment row chooses that shipment on the bill, once per click."""
    rows = list(getattr(getattr(picked, "selection", None), "rows", None) or [])
    ref = refs[rows[0]] if rows and 0 <= rows[0] < len(refs) else None
    if ref is not None and st.session_state.get("_clicked") != ref:
        st.session_state["_clicked"] = ref
        st.session_state["ship"] = ref


def _listed() -> None:
    """The shipment was changed from the list: clear the table's row tick to match."""
    st.session_state["_rows"] = st.session_state.get("_rows", 0) + 1
    st.session_state.pop("_clicked", None)


def _result(last: tuple[str, Raised] | None, ref: str) -> None:
    """What happened when this shipment's ticket was raised, as a line and a link onward."""
    if last is None or last[0] != ref:
        return
    raised = last[1]
    # A line, not a boxed notice: a box inside this card would be a card in a card.
    css = "result" if raised.ok else "result result--error"
    st.markdown(f'<p class="{css}">{escape(raised.message)}</p>', unsafe_allow_html=True)
    pages = st.session_state.get("pages")
    if raised.ok and raised.case_id and pages:  # pages is absent only in tests
        st.page_link(
            pages["trail"],
            label="Watch the agent work on it",
            icon=":material/route:",
            query_params={"case": raised.case_id},
        )
