"""The operations console (ADR 0044): `make run-console`, or hosted (ADR 0045).

Four screens in a top bar: raise a ticket (seller), the agent's steps, review (the human in
the loop) and the ledger (finance).

On the owner's machine it runs beside the dispute service (`make run-api`), reads the same
stores and posts tickets to that service over loopback. Hosted, `streamlit_app.py` at the
repository root runs the dispute service in the same process and hands `main` the function
that posts to it; there, a password stands in front of every screen and the console does not
open without one.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.ui.gate import opens
from dispute_agent.ui.screens import finance, reviewer, seller, trail
from dispute_agent.ui.theme import stylesheet

WORDMARK = Path(__file__).parent / "assets" / "wordmark.svg"

if TYPE_CHECKING:
    from collections.abc import Callable


def main(
    *,
    hosted: bool = False,
    post: Callable[[dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
    start: Callable[[], object] | None = None,
) -> None:
    st.set_page_config(page_title="Dispute desk", page_icon=":material/balance:", layout="wide")
    st.html(stylesheet())
    st.logo(str(WORDMARK), size="large")
    if hosted and not _through_the_gate():
        return
    if start is not None:
        try:
            start()  # the in-process dispute service, started once per process
        except ConfigError as missing:  # names the setting that is missing, never a value
            st.error(f"The dispute service could not start: {missing}")
            return
    if post is not None:
        st.session_state["post"] = post
    pages = {
        "seller": st.Page(
            seller.page,
            title="Raise a ticket",
            icon=":material/receipt_long:",
            # The default page is served at the root ("/"); Streamlit does not route its
            # url_path. The name stays: without it the top navigation does not draw there.
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


def _through_the_gate() -> bool:
    if st.session_state.get("gate_passed"):
        return True
    expected = Settings().console_password
    # A centred column, then one card (design handoff, Screen 0).
    _, middle, _ = st.columns([1, 1.6, 1])
    with middle:
        st.title("Dispute desk")
        if expected is None:  # no form: the console stays closed and says why
            st.error(
                "This hosted console has no password set (CONSOLE_PASSWORD), so it stays "
                "closed. It sends real email and opens real Jira issues."
            )
            return False
        with st.container(key="card-gate"):
            st.markdown(
                "A live dispute resolution agent on synthetic data. It sends real email to a test "
                "address and opens real Jira issues in a test project, so it is behind a password."
            )
            with st.form("gate", border=False):
                given = st.text_input(
                    "Site password", type="password", autocomplete="current-password"
                )
                if st.form_submit_button("Enter", type="primary"):
                    if opens(given, expected.get_secret_value()):
                        st.session_state["gate_passed"] = True
                        st.rerun()
                    st.error("That password is not correct.")
        st.caption("Synthetic data only: 14 made-up shipments and one test seller.")
    return False


if __name__ == "__main__":
    main()
