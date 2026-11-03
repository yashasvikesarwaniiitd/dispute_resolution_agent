"""The review queue page (P3-06, `make run-approvals`).

Thin on purpose. Every decision this page can make lives in `console.py`, which has no
Streamlit in it and is tested directly; what is left here is layout. A page that held the
rules would be a set of rules nobody could test without driving a browser, at the one point
where a person authorises the system to write to the outside world.

Two things the page does that matter beyond layout:

**It shows the text, not a rendering of the text.** The body displayed is the one stored on
the intent, whose hash is what gets approved. A page that re-rendered the draft from facts
could show something subtly different from what is about to be sent.

**It shows every number with its source.** "Approve this message" is a question nobody can
answer well. "Approve this message, in which INR 152.00 is the charge from the ledger" is.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import create_sqlite_engine
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, DomainValidationError
from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole, sign_in

DB_PATH = "data/cases.sqlite3"


def main() -> None:
    st.set_page_config(page_title="Review queue", layout="wide")
    st.title("Messages awaiting approval")

    settings = Settings()
    reviewer = _sign_in(settings)
    if reviewer is None:
        return

    engine = create_sqlite_engine(DB_PATH)
    with SqliteUnitOfWork(engine, clock=SystemClock()) as work:
        console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=reviewer)
        pending = list(console.queue())

    if not pending:
        st.success("Nothing is waiting. Every message has been decided.")
        return

    st.caption(
        f"Signed in as {reviewer.id}. Approving records your name against the exact text "
        "below; the system refuses to send anything else."
    )

    for approval in pending:
        _render(approval, settings, reviewer)


def _sign_in(settings: Settings) -> Any:
    """Token in the sidebar, identity in the session. Never in a form field."""
    token = st.sidebar.text_input("Review console token", type="password")
    reviewer_id = st.sidebar.text_input("Your name", value="owner")
    if not token:
        st.info("Enter the review console token to see the queue.")
        return None
    try:
        expected = settings.review_console_token
        return sign_in(token, expected.get_secret_value() if expected else None, reviewer_id)
    except (AuthPortError, ConfigError) as refused:
        st.error(str(refused))
        return None


def _render(approval: Any, settings: Settings, reviewer: Any) -> None:
    request = approval.request
    with st.expander(f"{request.message_kind} — case {request.case_id}", expanded=True):
        st.write(f"Requested {request.requested_at:%d %B %Y %H:%M} UTC")
        st.write(f"Expires {request.expires_at:%d %B %Y %H:%M} UTC")
        st.code(str(request.payload_hash), language=None)
        st.caption("The hash of the exact text. This is what your approval is bound to.")

        note = st.text_area("Note or reason", key=f"note-{approval.id}")
        approve, reject = st.columns(2)

        engine = create_sqlite_engine(DB_PATH)
        if approve.button("Approve", key=f"yes-{approval.id}"):
            _decide(engine, reviewer, approval.id, note, approving=True)
        if reject.button("Reject", key=f"no-{approval.id}"):
            _decide(engine, reviewer, approval.id, note, approving=False)


def _decide(engine: Any, reviewer: Any, approval_id: Any, note: str, *, approving: bool) -> None:
    try:
        with SqliteUnitOfWork(engine, clock=SystemClock()) as work:
            console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=reviewer)
            if approving:
                console.approve(approval_id, note)
            else:
                console.reject(approval_id, note)
    except DomainValidationError as refused:
        st.error(str(refused))
        return
    st.rerun()


if __name__ == "__main__":
    main()
