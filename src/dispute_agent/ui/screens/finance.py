"""Screen 4: a seller's ledger. Layout over `ui.finance` (ADR 0044).

Totals are sums of ledger rows and nothing else. Credits carry the case, the approver and
whether they were proved by read-back; the one place coral appears is a credited row.
"""

from __future__ import annotations

import streamlit as st

from dispute_agent.ui import data
from dispute_agent.ui.finance import rows, totals
from dispute_agent.ui.labels import when
from dispute_agent.ui.seller import catalogue, sellers
from dispute_agent.ui.theme import PAID


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
    charged.metric("Charged", f"INR {sums.charged:,.2f}")
    credited.metric("Credited back", f"INR {sums.credited:,.2f}")
    net.metric("Net billed", f"INR {sums.net:,.2f}")
    st.caption(
        "Every figure is a sum of ledger rows below. Credits show who approved them "
        "and whether the agent proved them by reading them back."
    )

    table = [
        {
            "Posted": when(r.posted_at),
            "Shipment": r.shipment_ref,
            "Type": r.kind,
            "Amount (INR)": float(r.amount),
            "Reason": r.reason,
            "Case": r.case_id,
            "Approved by": r.approved_by,
            "Proved": r.proved,
            "Transaction": r.transaction_id,
        }
        for r in found
    ]
    st.dataframe(
        _styled(table),
        hide_index=True,
        width="stretch",
        column_config={
            "Amount (INR)": st.column_config.NumberColumn(format="%.2f"),
        },
    )


def _styled(table: list[dict[str, object]]) -> object:
    import pandas as pd

    frame = pd.DataFrame(table)
    return frame.style.map(
        lambda v: f"color: {PAID}; font-weight: 600" if v == "Credit" else "", subset=["Type"]
    )
