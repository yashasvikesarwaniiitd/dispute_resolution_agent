"""Screen 4: a seller's ledger. Layout over `ui.finance` (ADR 0044).

Totals are sums of ledger rows and nothing else. The question finance asks first - which
credits did disputes produce, who approved each and was it proved - gets its own table;
every entry follows, filterable by kind. Money reads the same as on every other screen (₹).
The one place coral appears is a credit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.ui import data
from dispute_agent.ui.finance import Row, rows, totals
from dispute_agent.ui.labels import when
from dispute_agent.ui.seller import catalogue, sellers
from dispute_agent.ui.theme import PAID

if TYPE_CHECKING:
    from decimal import Decimal

KINDS = ("All", "Charges", "Credits")


def rupees(amount: Decimal) -> str:
    """₹1,820.00: the form every other screen uses for money."""
    return f"₹{amount:,.2f}"


def page() -> None:
    st.title("Ledger")
    shipments = catalogue()
    pick, _ = st.columns([2, 3])
    seller_ref = pick.selectbox("Seller", sellers(shipments))
    refs = [s.ref for s in shipments if s.seller_ref == seller_ref]

    entries = data.ledger_entries(refs)
    if not entries:
        st.markdown(
            '<p class="muted">The ledger has no entries yet. It is created and seeded with '
            "the synthetic charges when the dispute service starts.</p>",
            unsafe_allow_html=True,
        )
        return
    if data.has_store():
        with data.reader() as work:
            found = rows(entries, work)
    else:
        found = rows(entries, None)

    sums = totals(found)
    charged, credited, net = st.columns(3)
    charged.metric("Charged", rupees(sums.charged))
    credited.metric("Credited back", rupees(sums.credited))
    net.metric("Net billed", rupees(sums.net))
    st.caption("Every figure is a sum of the ledger rows below.")

    posted = [r for r in found if r.kind == "Credit"]
    st.header(f"Credits from disputes ({len(posted)})")
    if not posted:
        st.markdown(
            '<p class="muted">No credits yet. When a reviewer approves a credit and the agent '
            "posts it, it appears here with who approved it and whether it was proved.</p>",
            unsafe_allow_html=True,
        )
    else:
        st.dataframe(
            [
                {
                    "Posted": when(r.posted_at),
                    "Shipment": r.shipment_ref,
                    "Amount": rupees(r.amount),
                    "Reason": r.reason,
                    "Approved by": r.approved_by or "-",
                    "Proved": r.proved or "-",
                    "Case": r.case_id or "-",
                }
                for r in posted
            ],
            hide_index=True,
            width="stretch",
        )
        st.caption("Proved means the agent read the credit back from the ledger after posting it.")

    st.header("All entries")
    kind = st.segmented_control("Show", KINDS, default="All", key="ledger-kind") or "All"
    shown = _only(found, kind)
    st.dataframe(
        _styled(
            [
                {
                    "Posted": when(r.posted_at),
                    "Shipment": r.shipment_ref,
                    "Type": r.kind,
                    "Amount": rupees(r.amount),
                    "Reason": r.reason,
                    "Transaction": r.transaction_id,
                }
                for r in shown
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _only(found: list[Row], kind: str) -> list[Row]:
    if kind == "Charges":
        return [r for r in found if r.kind == "Charge"]
    if kind == "Credits":
        return [r for r in found if r.kind == "Credit"]
    return found


def _styled(table: list[dict[str, object]]) -> object:
    import pandas as pd

    frame = pd.DataFrame(table)
    if frame.empty:
        return frame
    return frame.style.map(
        lambda v: f"color: {PAID}; font-weight: 600" if v == "Credit" else "", subset=["Type"]
    ).set_properties(subset=["Amount"], **{"text-align": "right"})
