"""Screen 3: the human in the loop (design handoff 4, Screen 3; ADR 0044, 0046, 0047, 0048).

Sign-in is the review console's own (ADR 0018): the token is checked once and only the
reviewer's identity is kept for the session, never the token. Hosted, the site password has
already decided who may be here, so the screen skips the token (ADR 0047). Either way the
reviewer chooses one of the approvers the engine accepts (policy.yaml): no other name is
offered.

Each decision card shows what the agent recommends beside exactly what happens on approval -
the exact credit, or the exact email with every number's source - and records the decision
through `ReviewConsole`, bound to the stored text's hash. Nothing is sent from this page (I2).
Cases with a person are taken over or returned through `CaseDesk` (ADR 0046).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.actions import CreditPayload
from dispute_agent.kernel.domain.enums import ActionStatus
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, DomainValidationError
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.modules.approvals.streamlit_ui.console import (
    ReviewConsole,
    reviewer_named,
    sign_in,
)
from dispute_agent.packs.weight_dispute.requested_items import EVIDENCE_ITEMS
from dispute_agent.ui import case_sheet, data, photo_check
from dispute_agent.ui import review_view as view
from dispute_agent.ui.desk import CaseDesk, can_confirm_withdrawal
from dispute_agent.ui.labels import money, when
from dispute_agent.ui.photos import photo_for
from dispute_agent.ui.review import Card, not_yet_applied, queue, with_a_person
from dispute_agent.ui.theme import chip, escape, line
from dispute_agent.ui.trail import recommendation

if TYPE_CHECKING:
    from streamlit.delta_generator import DeltaGenerator

    from dispute_agent.kernel.domain.ids import ApprovalId
    from dispute_agent.kernel.domain.models import Actor, Case
    from dispute_agent.ui.seller import Ticket

LINES = "review_lines"  # what was decided this session, said in a line each
DESK_LINES = "desk_lines"  # what was done to cases with a person, said in a line each
#: Approvals decided this session: their cards leave at once, without waiting for the runner
#: to apply the decision (a few seconds), so a decision is never offered twice.
ACTED = "review_acted"
#: Cases with a person acted on this session, with the case's `updated_at` at that moment.
#: Hidden only until the agent changes the case, so a Return that comes back shows again.
ACTED_CASES = "review_acted_cases"


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
        acted = set(st.session_state.get(ACTED, ()))
        cards = [c for c in cards if str(c.item.approval.id) not in acted]
        people = not_yet_applied(with_a_person(work), st.session_state.get(ACTED_CASES, {}))
        extra = {c.item.approval.id: _credit_basis(work, c) for c in cards if c.item.is_credit}

    st.markdown(
        f'<p class="count">{escape(_count(cards, len(people)))}</p>', unsafe_allow_html=True
    )
    waiting, handed = st.tabs(
        [f"Awaiting your approval ({len(cards)})", f"With a person ({len(people)})"]
    )
    with waiting:
        for said in st.session_state.get(LINES, []):
            st.markdown(line(said), unsafe_allow_html=True)
        if not cards:
            st.markdown(
                '<p class="muted">Nothing is waiting. Every credit and message has been decided.</p>',
                unsafe_allow_html=True,
            )
        for card in cards:
            _card(card, reviewer, extra.get(card.item.approval.id, (None, money(Decimal(0)))))
    with handed:
        for said in st.session_state.get(DESK_LINES, []):
            st.markdown(line(said), unsafe_allow_html=True)
        if not people:
            st.markdown(
                '<p class="muted">No case is waiting for a person.</p>', unsafe_allow_html=True
            )
        for person_case in people:
            _person_card(person_case, reviewer)


def _signed_in() -> Actor | None:
    if "reviewer" in st.session_state:
        actor: Actor = st.session_state["reviewer"]
        note, switch = st.columns([5, 1], vertical_alignment="center")
        note.caption(
            f"Signed in as {actor.id.removeprefix('human:')}. Your name is recorded against each "
            "decision, bound to the exact amount or text shown."
        )
        if switch.button("Switch approver", type="tertiary", key="switch"):
            del st.session_state["reviewer"]
            st.rerun()
        return actor
    names = data.approvers()
    narrow, _ = st.columns([2, 3])
    if not names:
        narrow.error("No approver is listed in config/policy.yaml (approvals.approver_ids).")
        return None
    hosted = bool(st.session_state.get("gate_passed"))
    with narrow, st.container(key="card-signin"), st.form("sign-in", border=False):
        st.markdown(
            "Choose who you are reviewing as. That approver is recorded against each decision you make."
            if hosted
            else "Sign in with the review console token to see the queue."
        )
        token = "" if hosted else st.text_input("Review console token", type="password")
        name = st.selectbox(
            "Review as",
            names,
        )
        st.caption(
            "Only approvers listed in the policy appear here. The agent ignores decisions by anyone else."
        )
        if st.form_submit_button("Start reviewing" if hosted else "Sign in", type="primary"):
            try:
                if hosted:  # the site password stands in for the token (ADR 0047)
                    st.session_state["reviewer"] = reviewer_named(name or "")
                else:
                    expected = Settings().review_console_token
                    st.session_state["reviewer"] = sign_in(
                        token, expected.get_secret_value() if expected else None, name or ""
                    )
                st.rerun()
            except AuthPortError:
                st.error("The review console token is not correct.")
            except (ConfigError, DomainValidationError) as refused:
                st.error(str(refused))
    return None


def _count(cards: list[Card], people: int) -> str:
    """What waits for this reviewer, in one line, from the queue itself."""
    amounts = [c.item.credit_amount for c in cards if c.item.is_credit and c.item.credit_amount]
    emails = sum(1 for c in cards if not c.item.is_credit)
    parts = [f"a {amount} credit" for amount in amounts]
    if emails:
        parts.append(f"{emails} email{'s' if emails != 1 else ''}")
    waiting = (
        f"{len(cards)} waiting for your decision: {', '.join(parts)}."
        if cards
        else "Nothing waiting for your decision."
    )
    handed = f" {people} case{'s' if people != 1 else ''} with a person." if people else ""
    return waiting + handed


def _credit_basis(work: object, card: Card) -> tuple[str | None, str]:
    """The duplicate debit a duplicate-charge credit returns, and what is already credited."""
    case_id = card.item.approval.request.case_id
    latest = work.assessments.latest(case_id)  # type: ignore[attr-defined]
    dup = latest.duplicate_debit_total if latest is not None else None
    already = Decimal(0)
    for action in work.actions.for_case(case_id):  # type: ignore[attr-defined]
        if action.status is not ActionStatus.VERIFIED:
            continue
        try:
            payload = work.intents.get(action.intent_id).payload  # type: ignore[attr-defined]
        except Exception:  # a plan that cannot be read adds nothing it cannot prove
            continue
        if isinstance(payload, CreditPayload):
            already += payload.amount.amount
    duplicate = (
        render_fact(dup)
        if dup is not None and card.item.credit_reason == "duplicate_charge"
        else None
    )
    return duplicate, money(already)


def _header(kind: str, ticket: Ticket, since: str, case_id: str) -> None:
    left, right = st.columns([3, 1], vertical_alignment="center")
    left.markdown(
        f'<div class="card-headrow"><h2 class="card-kind">{escape(kind)}</h2>'
        f'<span class="mono">{escape(ticket.shipment_ref)}</span>{chip(ticket.label, ticket.tone)}'
        f'<span class="muted">{escape(since)}</span></div>',
        unsafe_allow_html=True,
    )
    pages = st.session_state.get("pages")
    if pages:  # absent only when a screen runs on its own, as in tests
        with right:
            st.page_link(
                pages["trail"],
                label="See every step of this case",
                icon=":material/route:",
                query_params={"case": case_id},
            )


def _card(card: Card, reviewer: Actor, basis: tuple[str | None, str]) -> None:
    item, ticket = card.item, card.case
    aid = item.approval.id
    request = item.approval.request
    with st.container(key=f"card-{aid}"):
        hours = round((request.expires_at - request.requested_at).total_seconds() / 3600)
        _header(
            view.kind_title(item),
            ticket,
            f"requested {when(request.requested_at)} · expires after {hours} h",
            ticket.case_id,
        )
        st.markdown('<hr class="rule">', unsafe_allow_html=True)
        left, right = st.columns(2, gap="large")
        left.markdown(view.recommends(card.recommendation), unsafe_allow_html=True)
        _photo(left, ticket)
        with right, st.container(key=f"panel-{aid}"):
            if item.is_credit:
                proposer = (
                    "AI"
                    if card.recommendation and card.recommendation.by.startswith("AI")
                    else "rules engine"
                )
                st.markdown(
                    view.if_approve_credit(
                        item, proposer=proposer, duplicate=basis[0], already=basis[1]
                    ),
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(view.if_approve_email(item), unsafe_allow_html=True)
        st.markdown('<hr class="rule">', unsafe_allow_html=True)
        if not item.matches_approval:
            st.markdown(
                '<div class="integrity" role="alert"><p><strong>The stored text has changed since '
                "approval was requested.</strong> It cannot be approved. Report this.</p></div>",
                unsafe_allow_html=True,
            )
            return
        # One form, so the note and the button arrive together: a text box only reports
        # what was typed when it loses focus, which used to swallow the first click.
        with st.form(f"decide-{aid}", border=False):
            note = st.text_area("Note, or your reason for rejecting", key=f"note-{aid}", height=68)
            st.caption("A note is optional to approve. A reason is needed to reject.")
            row = st.container(horizontal=True, gap="small")
            yes = row.form_submit_button(view.approve_label(item), type="primary")
            no = row.form_submit_button("Reject")
        if yes:
            _decide(reviewer, card, note, approving=True)
        elif no and not note.strip():
            st.error("Write your reason for rejecting, then press Reject.")
        elif no:
            _decide(reviewer, card, note, approving=False)


def _photo(column: DeltaGenerator, ticket: Ticket) -> None:
    """The parcel photo, and what the photo check saw if it has run (ADR 0049), on any card."""
    photo = photo_for(ticket.shipment_ref)
    if photo is not None:  # the thing a person is being asked to judge
        column.image(str(photo), caption="Parcel photo", width="stretch")
    with data.reader() as work:
        case = work.cases.get(ticket.case_id)  # type: ignore[arg-type]
        checked = photo_check.latest(work.evidence.bundle(case.id, case.gather_round).items)
    if checked is not None:
        column.markdown(photo_check.html(checked), unsafe_allow_html=True)


def _person_card(ticket: Ticket, reviewer: Actor) -> None:
    """One case with a person: the task, the facts, and the decisions (ADR 0046, ADR 0050)."""
    with data.reader() as work:
        case = work.cases.get(ticket.case_id)  # type: ignore[arg-type]
        proposals = [p for p in work.proposals.for_case(case.id) if p.reasoner != "human"]
        paid = work.actions.has_verified_credit(case.id)
        sheet = case_sheet.build(work, case)
    review = case.review_reason
    rec = recommendation(proposals[-1]) if proposals else None
    key = ticket.case_id
    with st.container(key=f"card-person-{key}"):
        _header("Shipment", ticket, f"since {when(ticket.updated_at)}", key)
        st.markdown('<hr class="rule">', unsafe_allow_html=True)
        left, right = st.columns(2, gap="large")
        if review is not None:
            left.markdown(view.person_reason(review.code, review.detail), unsafe_allow_html=True)
        left.markdown(case_sheet.task_html(sheet), unsafe_allow_html=True)
        _photo(left, ticket)
        with right, st.container(key=f"panel-person-{key}"):
            st.markdown(case_sheet.facts_html(sheet), unsafe_allow_html=True)
            st.markdown(view.last_proposal(rec), unsafe_allow_html=True)
        st.markdown('<hr class="rule">', unsafe_allow_html=True)
        _decision_form(ticket, reviewer, case, sheet, paid=paid)


#: The desk's own choices, after the verdicts code's menu allows (ADR 0046).
TAKE_OVER = "Take it over"
RETURN = "Return it to the agent"
WITHDRAW = "Confirm the seller's withdrawal"
_DESK_EFFECT = {
    TAKE_OVER: "You handle it outside the agent from here; the agent stops. Tick the box below "
    "to tell the seller (that email goes through review first).",
    RETURN: "The agent starts a new round from fresh evidence. On a photo case a vision model "
    "also checks the photo, and the case comes back here with its verdict.",
    WITHDRAW: "The case closes as withdrawn, as the seller asked.",
}


def _decision_form(
    ticket: Ticket, reviewer: Actor, case: Case, sheet: case_sheet.Sheet, *, paid: bool
) -> None:
    key = ticket.case_id
    options = [v.label for v in sheet.verdicts] + [TAKE_OVER]
    if not paid:
        options.append(RETURN)
    if can_confirm_withdrawal(case):
        options.append(WITHDRAW)
    effects = {v.label: v.effect for v in sheet.verdicts} | _DESK_EFFECT
    st.markdown("<h3>Your decision</h3>", unsafe_allow_html=True)
    if sheet.no_verdict:
        st.caption(sheet.no_verdict)
    with st.form(f"desk-{key}", border=False):
        choice = st.radio(
            "Your decision",
            options,
            index=None,
            captions=[effects[o] for o in options],
            key=f"choice-{key}",
            label_visibility="collapsed",
        )
        items = st.multiselect(
            "Only for Ask the seller for evidence: what to ask for",
            list(EVIDENCE_ITEMS),
            key=f"items-{key}",
        )
        notify = st.checkbox(
            "Only for Take it over: tell the seller a person is handling it",
            key=f"notify-{key}",
        )
        note = st.text_area(
            "Why you decided this (kept on the case's record)", key=f"person-note-{key}", height=68
        )
        sent = st.form_submit_button("Record my decision", type="primary")
        if paid:
            st.caption(
                "A credit on this case is already verified, so it cannot go back to the agent."
            )
    if sent:
        _record(ticket, reviewer, sheet, choice, note, tuple(items), notify=notify, paid=paid)


def _record(
    ticket: Ticket,
    reviewer: Actor,
    sheet: case_sheet.Sheet,
    choice: str | None,
    note: str,
    items: tuple[str, ...],
    *,
    notify: bool,
    paid: bool,
) -> None:
    """The chosen decision, recorded through the desk (checked again there and by dispatch)."""
    if choice is None:
        st.error("Choose a decision first.")
        return
    if choice == TAKE_OVER:
        _desk(
            reviewer,
            ticket,
            lambda desk, c: desk.take_over(c, notify_seller=notify, note=note),
            said=f"took over {ticket.shipment_ref}. The agent has stopped."
            + (" The email telling the seller now waits for your approval." if notify else ""),
        )
        return
    if not note.strip():
        st.error("Write why you decided this first: it is recorded with the case.")
        return
    if choice == RETURN:
        _desk(
            reviewer,
            ticket,
            lambda desk, c: desk.return_to_agent(c, note, credit_verified=paid),
            said=f"returned {ticket.shipment_ref} to the agent. It starts a new round from "
            "fresh evidence; on a photo case that round also runs the photo check, and the "
            "case comes back here with its verdict.",
        )
        return
    if choice == WITHDRAW:
        _desk(
            reviewer,
            ticket,
            lambda desk, c: desk.confirm_withdrawal(c, note),
            said=f"confirmed the seller's withdrawal on {ticket.shipment_ref}.",
        )
        return
    verdict = next((v for v in sheet.verdicts if v.label == choice), None)
    if verdict is None:
        st.error("That decision is no longer offered. Refresh the page.")
        return
    asking = verdict.action == "request_seller_evidence"
    if asking and not items:
        st.error("Tick at least one item to ask the seller for.")
        return
    _desk(
        reviewer,
        ticket,
        lambda desk, c: desk.decide(
            c,
            verdict.action,
            note,
            reason_code=verdict.reason_code,
            requested_items=items if asking else (),
        ),
        said=f"decided {ticket.shipment_ref}: {verdict.label[0].lower()}{verdict.label[1:]}. "
        f"{verdict.effect} Anything that needs approval appears under Awaiting your approval.",
    )


def _said(text: str) -> None:
    st.session_state.setdefault(LINES, []).insert(0, text)


def _desk(reviewer: Actor, ticket: Ticket, act: object, *, said: str) -> None:
    try:
        with data.writer() as work:
            case = work.cases.get(ticket.case_id)  # type: ignore[arg-type]
            act(CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=reviewer), case)  # type: ignore[operator]
    except DomainValidationError as refused:
        st.error(str(refused))
        return
    who = reviewer.id.removeprefix("human:")
    st.session_state.setdefault(DESK_LINES, []).insert(
        0, f"{who} {said} Recorded; the agent applies it within a few seconds."
    )
    st.session_state.setdefault(ACTED_CASES, {})[ticket.case_id] = ticket.updated_at.isoformat()
    st.toast("Recorded. The agent applies it within a few seconds.")
    st.rerun()


def _decide(reviewer: Actor, card: Card, note: str, *, approving: bool) -> None:
    approval_id: ApprovalId = card.item.approval.id
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
    who = reviewer.id.removeprefix("human:")
    thing = f"{view.what(card.item)} on {card.case.shipment_ref}"
    _said(
        f"{who} approved {thing}. Recorded. The agent picks it up from here."
        if approving
        else f"{who} rejected {thing}. Reason: “{note.strip()}”. The agent re-plans. A case "
        "allows two rejections before it goes to a person."
    )
    st.session_state.setdefault(ACTED, []).append(str(approval_id))
    st.toast("Recorded. The agent picks it up from here.")
    st.rerun()
