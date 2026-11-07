"""Screen 3: the human in the loop. Layout over `ui.review` and `ReviewConsole` (ADR 0044).

Sign-in is the review console's own (ADR 0018): the token is checked once and only the
reviewer's identity is kept for the session, never the token. The decision is recorded by
`ReviewConsole`, bound to the stored text's hash; nothing is sent from this page (I2).

Cases handed to a person can be taken over, returned to the agent or - when the seller asked
- closed as withdrawn, through `CaseDesk` (ADR 0046): recorded as the signed-in reviewer and
applied by the runner within a few seconds, through the engine's own transitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, DomainValidationError
from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole, sign_in
from dispute_agent.ui import data
from dispute_agent.ui.desk import CaseDesk, can_confirm_withdrawal
from dispute_agent.ui.review import Card, queue, with_a_person
from dispute_agent.ui.theme import chip, escape
from dispute_agent.ui.trail import recommendation

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.ids import ApprovalId
    from dispute_agent.kernel.domain.models import Actor
    from dispute_agent.ui.seller import Ticket


def page() -> None:
    st.title("Review")
    reviewer = _signed_in()
    if reviewer is None:
        return
    if not data.has_store():
        st.info(data.NO_STORE)
        return

    with data.reader() as work:
        console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=reviewer)
        cards = queue(work, console)
        people = with_a_person(work)

    waiting, handed = st.tabs(
        [f"Awaiting your approval ({len(cards)})", f"With a person ({len(people)})"]
    )
    with waiting:
        if not cards:
            st.markdown(
                '<p class="muted">Nothing is waiting. Every credit and message has been '
                "decided.</p>",
                unsafe_allow_html=True,
            )
        for card in cards:
            _card(card, reviewer)
    with handed:
        if not people:
            st.markdown(
                '<p class="muted">No case is waiting for a person.</p>', unsafe_allow_html=True
            )
        for person_case in people:
            _person_card(person_case, reviewer)


def _signed_in() -> Actor | None:
    if "reviewer" in st.session_state:
        actor: Actor = st.session_state["reviewer"]
        st.caption(
            f"Signed in as {actor.id.removeprefix('human:')}. Your name is recorded "
            "against each decision, bound to the exact amount or text shown."
        )
        return actor
    with st.form("sign-in"):
        st.markdown("Sign in with the review console token to see the queue.")
        token = st.text_input("Review console token", type="password")
        name = st.text_input("Your name", value="owner")
        if st.form_submit_button("Sign in", type="primary"):
            try:
                expected = Settings().review_console_token
                st.session_state["reviewer"] = sign_in(
                    token,
                    expected.get_secret_value() if expected else None,
                    name.strip() or "owner",
                )
                st.rerun()
            except (AuthPortError, ConfigError) as refused:
                st.error(str(refused))
    return None


def _card(card: Card, reviewer: Actor) -> None:
    item, case = card.item, card.case
    with st.container(border=True):
        st.markdown(
            f"**{escape('Credit' if item.is_credit else 'Message: ' + item.kind.replace('_', ' '))}**"
            f" &nbsp; shipment {escape(case.shipment_ref)} &nbsp; {chip(case.label, case.tone)}",
            unsafe_allow_html=True,
        )
        ai, act = st.columns(2, gap="large")
        with ai:
            st.markdown("**What the agent recommends**")
            rec = card.recommendation
            if rec is None:
                st.markdown(
                    '<p class="muted">No proposal is recorded for this item.</p>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"{escape(rec.action)}, confidence {escape(rec.confidence)}  \n"
                    f'<span class="muted">{escape(rec.by)}</span>',
                    unsafe_allow_html=True,
                )
                st.caption("Reasoning, exactly as recorded:")
                st.text(rec.rationale)
                for kind, statement, refs in rec.findings:
                    cited = f" (cites {', '.join(refs)})" if refs else ""
                    st.text(f"{kind}: {statement}{cited}")
        with act:
            st.markdown("**If you approve**")
            if not item.matches_approval:
                st.error(
                    "The stored text has changed since approval was requested. It cannot "
                    "be approved. Report this."
                )
                return
            if item.is_credit:
                st.markdown(
                    f"A credit of **{escape(item.credit_amount)}** is posted to the ledger "
                    f"for {escape((item.credit_reason or '').replace('_', ' '))}."
                )
                st.caption(item.body)
                st.markdown("**Code's figures behind it**")
                for label, value in item.evidence:
                    st.text(f"{label}: {value}")
            else:
                st.markdown(f"An email to {escape(item.to)}")
                st.text(f"Subject: {item.subject}")
                st.text(item.body)
                st.markdown("**Every number in it, and where it came from**")
                for figure, source in item.numbers or (("(none)", "this message has no figures"),):
                    st.text(f"{figure}: {source}")
        note = st.text_area("Note, or your reason for rejecting", key=f"note-{item.approval.id}")
        yes, no, _ = st.columns([1, 1, 3])
        if yes.button("Approve", key=f"yes-{item.approval.id}", type="primary"):
            _decide(reviewer, item.approval.id, note, approving=True)
        if no.button("Reject", key=f"no-{item.approval.id}"):
            _decide(reviewer, item.approval.id, note, approving=False)


def _person_card(ticket: Ticket, reviewer: Actor) -> None:
    """One case with a person: why, what the agent last proposed, and the three decisions."""
    with data.reader() as work:
        case = work.cases.get(ticket.case_id)  # type: ignore[arg-type]
        proposals = list(work.proposals.for_case(case.id))
        paid = work.actions.has_verified_credit(case.id)
    reason = case.review_reason
    with st.container(border=True):
        st.markdown(
            f"**Shipment {escape(ticket.shipment_ref)}** &nbsp; {chip(ticket.label, ticket.tone)}"
            f' &nbsp; <span class="muted">since {ticket.updated_at:%d %b %H:%M}</span>',
            unsafe_allow_html=True,
        )
        if reason is not None:
            why = reason.code.value.replace("_", " ")
            st.markdown(f"Why it is with a person: **{escape(why)}**")
            if reason.detail:
                st.text(reason.detail)
        if proposals:
            rec = recommendation(proposals[-1])
            st.markdown(
                f'<span class="muted">Last proposal: {escape(rec.action)}, {escape(rec.by)}</span>',
                unsafe_allow_html=True,
            )
        pages = st.session_state.get("pages")
        if pages:  # absent only when a screen runs on its own, as in tests
            st.page_link(
                st.session_state["pages"]["trail"],
                label="See every step of this case",
                query_params={"case": ticket.case_id},
            )
        key = ticket.case_id
        note = st.text_area(
            "Your note (needed to return it to the agent)", key=f"person-note-{key}"
        )
        notify = st.checkbox(
            "When taking over, tell the seller a person is handling it (the email goes "
            "through review first)",
            key=f"notify-{key}",
        )
        take, back, withdraw = st.columns(3)
        if take.button("Take over", key=f"take-{key}", type="primary"):
            _desk(reviewer, key, lambda desk, c: desk.take_over(c, notify_seller=notify, note=note))
        if back.button("Return to the agent", key=f"back-{key}", disabled=paid):
            _desk(
                reviewer,
                key,
                lambda desk, c: desk.return_to_agent(c, note, credit_verified=paid),
            )
        if can_confirm_withdrawal(case) and withdraw.button(
            "Confirm withdrawal", key=f"withdraw-{key}"
        ):
            _desk(reviewer, key, lambda desk, c: desk.confirm_withdrawal(c, note))
        if paid:
            st.caption(
                "A credit on this case is already verified, so it cannot go back to the agent."
            )


def _desk(reviewer: Actor, case_id: str, act: object) -> None:
    try:
        with data.writer() as work:
            case = work.cases.get(case_id)  # type: ignore[arg-type]
            act(CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=reviewer), case)  # type: ignore[operator]
    except DomainValidationError as refused:
        st.error(str(refused))
        return
    st.toast("Recorded. The agent applies it within a few seconds.")
    st.rerun()


def _decide(reviewer: Actor, approval_id: ApprovalId, note: str, *, approving: bool) -> None:
    try:
        with data.writer() as work:
            console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=reviewer)
            if approving:
                console.approve(approval_id, note)
            else:
                console.reject(approval_id, note)
    except DomainValidationError as refused:
        st.error(str(refused))
        return
    st.toast("Recorded. The agent picks it up from here.")
    st.rerun()
