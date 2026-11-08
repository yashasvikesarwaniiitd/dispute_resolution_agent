"""Screen 4: a seller's ledger (design handoff 4, Screen 4; ADR 0044, ADR 0048).

Totals are sums of ledger rows and nothing else (I9 for the screen), computed here from the
rows and never stored. The question finance asks first - which credits did disputes produce,
who approved each, and was it proved - gets its own table, each credit linked to its case's
steps. Every entry follows, filterable by kind; the opening weight charges, one per shipment,
fold into one group. The one place coral appears is a credit.
"""

from __future__ import annotations

from collections import Counter

import streamlit as st

from dispute_agent.ui import data, picker
from dispute_agent.ui.finance import Row, rows, totals
from dispute_agent.ui.labels import money, when
from dispute_agent.ui.seller import catalogue, sellers
from dispute_agent.ui.theme import escape

KINDS = ("All", "Charges", "Credits")
GRID_CREDITS = "1.5fr 1fr 1fr 1.2fr 1fr 1.3fr 1fr"
GRID_ENTRIES = "1.5fr 1fr .8fr 1fr 1.2fr 1.6fr"


def page() -> None:
    title, pick = st.columns([3, 1], vertical_alignment="bottom")
    title.title("Ledger")
    shipments = catalogue()
    seller_ref = pick.selectbox("Seller", sellers(shipments))
    refs = [s.ref for s in shipments if s.seller_ref == seller_ref]

    entries = data.ledger_entries(refs)
    if not entries:
        st.markdown(
            '<p class="muted">The ledger has no entries yet. It is created and seeded with the '
            "synthetic charges when the dispute service starts.</p>",
            unsafe_allow_html=True,
        )
        return
    if data.has_store():
        with data.reader() as work:
            found = rows(entries, work)
    else:
        found = rows(entries, None)

    sums = totals(found)
    with st.container(key="card-totals"):
        st.markdown(
            '<dl class="totals">'
            f"<div><dt>Charged</dt><dd>{escape(money(sums.charged))}</dd></div>"
            f"<div><dt>Credited back</dt><dd>\u2212 {escape(money(sums.credited))}</dd></div>"
            f"<div><dt>Net billed</dt><dd>= {escape(money(sums.net))}</dd></div></dl>",
            unsafe_allow_html=True,
        )
        st.caption("Every figure is a sum of the ledger rows below.")

    credits_ = [r for r in found if r.kind == "Credit"]
    with st.container(key="card-credits"):
        st.header(f"Credits from disputes ({len(credits_)})")
        if not credits_:
            st.markdown(
                '<p class="muted">No credits yet. When a reviewer approves a credit and the agent '
                "posts it, it appears here with who approved it and whether it was proved.</p>",
                unsafe_allow_html=True,
            )
        else:
            _credits(credits_)
            st.caption(
                "Proved means the agent read the credit back from the ledger after posting it. "
                'A credit the agent did not post reads "Not posted by the agent".'
            )

    with st.container(key="card-entries"):
        head, show = st.columns([1, 1], vertical_alignment="bottom")
        head.header("All entries")
        with show:
            kind = (
                st.segmented_control(
                    "Show", KINDS, default="All", key="ledger-kind", label_visibility="collapsed"
                )
                or "All"
            )
        _entries(found, kind)


def _credits(posted: list[Row]) -> None:
    clicked = picker.show(
        {
            "label": "Credits from disputes",
            "grid": GRID_CREDITS,
            "columns": [
                {"label": "Posted"},
                {"label": "Shipment"},
                {"label": "Amount", "align": "right"},
                {"label": "Reason"},
                {"label": "Approved by"},
                {"label": "Proved"},
                {"label": "Case"},
            ],
            "rows": [
                {
                    "id": r.transaction_id,
                    "cells": [
                        {"kind": "muted", "text": when(r.posted_at)},
                        {"kind": "num", "text": r.shipment_ref},
                        {"kind": "paid", "text": money(r.amount)},
                        r.reason,
                        {"actor": r.approved_by, "style": "person"} if r.approved_by else "-",
                        {
                            "kind": "text",
                            "text": "✓ Read back" if r.proved == "Read back" else (r.proved or "-"),
                        },
                        (
                            {
                                "link": "See the steps →",
                                "target": r.case_id,
                                "label": f"See the agent's steps for {r.shipment_ref}",
                            }
                            if r.case_id
                            else "-"
                        ),
                    ],
                }
                for r in posted
            ],
        },
        key="ledger-credits",
    )
    pages = st.session_state.get("pages")
    if clicked["open"] and pages:
        st.switch_page(pages["trail"], query_params={"case": clicked["open"]})


def _entries(found: list[Row], kind: str) -> None:
    credits_ = [r for r in found if r.kind == "Credit"]
    charges = [r for r in found if r.kind == "Charge"]
    shown_credits = credits_ if kind in ("All", "Credits") else []
    shown_charges = charges if kind in ("All", "Charges") else []
    if shown_credits:
        _table(shown_credits, "ledger-entries-credits")
    if not shown_charges:
        if not shown_credits:
            st.markdown('<p class="muted">No entries in this view.</p>', unsafe_allow_html=True)
        return
    # The opening charges share one posting time, one per shipment: fold them into a group.
    moment, count = Counter(r.posted_at for r in shown_charges).most_common(1)[0]
    if count > 1:
        grouped = [r for r in shown_charges if r.posted_at == moment]
        total = money(sum((r.amount for r in grouped), start=grouped[0].amount * 0))
        label = f"Weight charges posted {when(moment)} · one per shipment · {total}"
        rest = [r for r in shown_charges if r.posted_at != moment]
        if rest:
            _table(rest, "ledger-entries-rest")
        with st.expander(label, expanded=kind == "Charges"):
            _table(grouped, "ledger-entries-group")
    else:
        _table(shown_charges, "ledger-entries-charges")


def _table(shown: list[Row], key: str) -> None:
    picker.show(
        {
            "label": "Ledger entries",
            "grid": GRID_ENTRIES,
            "columns": [
                {"label": "Posted"},
                {"label": "Shipment"},
                {"label": "Type"},
                {"label": "Amount", "align": "right"},
                {"label": "Reason"},
                {"label": "Transaction"},
            ],
            "rows": [
                {
                    "id": r.transaction_id,
                    "cells": [
                        {"kind": "muted", "text": when(r.posted_at)},
                        {"kind": "num", "text": r.shipment_ref},
                        {"kind": "paid", "text": "Credit"} if r.kind == "Credit" else "Charge",
                        {"kind": "paid" if r.kind == "Credit" else "num", "text": money(r.amount)},
                        r.reason,
                        {"kind": "muted", "text": r.transaction_id},
                    ],
                }
                for r in shown
            ],
        },
        key=key,
    )
