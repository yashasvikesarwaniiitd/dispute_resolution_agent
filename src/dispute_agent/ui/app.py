"""The operations console (ADR 0044): `make run-console`.

Four screens in a top bar: raise a ticket (seller), the agent's steps, review (the human in
the loop) and the ledger (finance). Run it beside the dispute service (`make run-api`); the
console reads the same stores and posts tickets to that service.
"""

from __future__ import annotations

import streamlit as st

from dispute_agent.ui.screens import finance, reviewer, seller, trail
from dispute_agent.ui.theme import stylesheet


def main() -> None:
    st.set_page_config(page_title="Dispute desk", page_icon=":material/balance:", layout="wide")
    st.html(stylesheet())
    pages = {
        "seller": st.Page(
            seller.page,
            title="Raise a ticket",
            icon=":material/receipt_long:",
            url_path="seller",
            default=True,
        ),
        "trail": st.Page(
            trail.page, title="Agent steps", icon=":material/route:", url_path="steps"
        ),
        "review": st.Page(
            reviewer.page, title="Review", icon=":material/fact_check:", url_path="review"
        ),
        "ledger": st.Page(
            finance.page, title="Ledger", icon=":material/account_balance:", url_path="ledger"
        ),
    }
    st.session_state["pages"] = pages
    st.navigation(list(pages.values()), position="top").run()


main()
