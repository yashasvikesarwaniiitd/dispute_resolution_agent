"""Screen 2: the agent's steps for one case. Layout over `ui.trail` and `ui.journey`.

Read top to bottom the way a person asks (UX pass, docs/design/REDESIGN_HALLMARK.md):

1. Which case is this? The shipment, its status, the seller, when it opened and closed.
2. Where does it stand? One headline, then why and what happens next, and a link to the
   screen where the next move is made.
3. How far along is it? Five phases on a stepper, each in words: done, in progress,
   waiting, with a person, not needed, not reached.
4. What did code find? The charged, correct and overcharged figures - only when the case
   was eligible, so they answer a question someone asked.
5. What happened? A timeline, round by round: each step says what happened in one line,
   who did it and when, with details folded away. The investigator's reasoning is shown
   openly, as recorded; the service's bookkeeping is shown quietly.

Everything interpolated into markup goes through `escape`; model-written text stays text.
The case lives in the URL (`?case=`); an open case refreshes itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.journey import Journey, Mark, journey
from dispute_agent.ui.labels import Tone, when
from dispute_agent.ui.seller import tickets
from dispute_agent.ui.theme import chip, escape
from dispute_agent.ui.trail import Actor, Step, Trail, build

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.models import Case

REFRESH_S = 4

#: The state word under each phase: words, so the stepper never relies on colour alone.
MARK_WORD = {
    Mark.DONE: "done",
    Mark.CURRENT: "in progress",
    Mark.WAITING: "waiting",
    Mark.PERSON: "with a person",
    Mark.SKIPPED: "not needed",
    Mark.NOT_REACHED: "not reached",
    Mark.TODO: "later",
}
#: Who did a step, as a reader would name them.
ACTOR = {
    Actor.CODE: "Code",
    Actor.AI: "AI",
    Actor.RULES: "Rules engine",
    Actor.PERSON: "Reviewer",
    Actor.SELLER: "Seller",
    Actor.HUB: "Hub",
    Actor.SYSTEM: "System",
}
LEGEND = (
    "Code: fixed checks and calculations, no AI. AI or rules engine: proposes what to do. "
    "Reviewer: a person who approves. System: the service's own bookkeeping. Times are UTC."
)
MARKS = {Tone.SETTLED: "Credited", Tone.ATTENTION: "Needs a person"}


def page() -> None:
    st.title("The agent's steps")
    if not data.has_store():
        st.info(data.NO_STORE)
        return
    with data.reader() as work:
        everyone = tickets(work.cases.list_in_states(list(CaseState)))
    if not everyone:
        st.markdown(
            '<p class="muted">No cases yet. Raise a ticket on the seller screen and '
            "its steps appear here.</p>",
            unsafe_allow_html=True,
        )
        pages = st.session_state.get("pages")
        if pages:
            st.page_link(pages["seller"], label="Raise a ticket")
        return

    ids = [t.case_id for t in everyone]
    wanted = st.query_params.get("case") or st.session_state.get("case")
    pick, _ = st.columns([2, 3])
    case_id = pick.selectbox(
        f"Case ({len(ids)})",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=lambda cid: next(
            f"{t.shipment_ref} · {t.label} · opened {when(t.opened_at)}"
            for t in everyone
            if t.case_id == cid
        ),
    )
    st.query_params["case"] = case_id
    is_open = next(t.is_open for t in everyone if t.case_id == case_id)
    st.fragment(_case, run_every=REFRESH_S if is_open else None)(case_id)


def _case(case_id: str) -> None:
    with data.reader() as work:
        case = work.cases.get(case_id)  # type: ignore[arg-type]
        trail = build(work, case, usage=data.ai_usage(case.id))
        where = journey(work, case)
    with st.container(border=True, key="stock-case"):
        st.markdown(_identity(case, trail), unsafe_allow_html=True)
        st.markdown(_stands(where), unsafe_allow_html=True)
        _onward(case)
        st.markdown(_stepper(where), unsafe_allow_html=True)
        if where.figures:
            st.markdown(_figures(where), unsafe_allow_html=True)
    st.header("What happened")
    st.markdown(f'<p class="muted">{escape(LEGEND)}</p>', unsafe_allow_html=True)
    st.markdown(_timeline(trail, case.opened_at), unsafe_allow_html=True)


def _identity(case: Case, trail: Trail) -> str:
    """The case's name line and its facts line."""
    calls = (
        f"{trail.ai_calls} AI call{'s' if trail.ai_calls != 1 else ''} (USD {trail.ai_cost:.4f})"
        if trail.ai_calls
        else "no AI calls"
    )
    facts = [
        f"Seller {case.facts.seller_ref}",
        f"opened {when(case.opened_at)}",
        f"closed {when(case.closed_at)}" if case.closed_at else "open",
        calls,
    ]
    if trail.is_open:
        facts.append(f"refreshes every {REFRESH_S} s")
    return (
        f'<div class="case-head"><span class="case-ref">{escape(trail.shipment_ref)}</span>'
        f"{chip(trail.label, trail.tone)}</div>"
        f'<p class="muted case-facts">{escape(" · ".join(facts))}</p>'
    )


def _stands(where: Journey) -> str:
    rows = ([("Why", where.why)] if where.why else []) + [("Next", where.next_step)]
    body = "".join(f"<dt>{escape(k)}</dt><dd>{escape(v)}</dd>" for k, v in rows)
    return f'<p class="headline">{escape(where.headline)}</p><dl class="pair stands">{body}</dl>'


def _onward(case: Case) -> None:
    """A link to the screen where the next move is made, when there is one."""
    pages = st.session_state.get("pages")
    if not pages:  # absent only when a screen runs on its own, as in tests
        return
    if case.state in (CaseState.AWAITING_APPROVAL, CaseState.NEEDS_HUMAN_REVIEW):
        st.page_link(
            pages["review"], label="Decide on the Review screen", icon=":material/fact_check:"
        )
    elif case.state is CaseState.RESOLVED_CREDITED:
        st.page_link(
            pages["ledger"], label="See the credit in the ledger", icon=":material/account_balance:"
        )


def _stepper(where: Journey) -> str:
    settled = where.tone is Tone.SETTLED
    items = []
    for name, mark in where.phases:
        style = "settled" if settled and name == "Closed" else mark.value
        items.append(
            f'<li class="step step--{style}"><b>{escape(name)}</b>'
            f"<small>{escape(MARK_WORD[mark])}</small></li>"
        )
    return f'<ol class="stepper" aria-label="Progress">{"".join(items)}</ol>'


def _figures(where: Journey) -> str:
    """Code's own figures for the case: a label and a tabular figure each."""
    cells = "".join(
        f"<div><dt>{escape(label)}</dt><dd>{escape(value)}</dd></div>"
        for label, value in where.figures
    )
    return f'<dl class="figs" aria-label="Code\'s figures">{cells}</dl>'


def _text(value: str) -> str:
    """Escaped, with line breaks kept: recorded text is shown as written, never as markup."""
    return escape(value).replace("\n", "<br>")


def _timeline(trail: Trail, opened: datetime) -> str:
    out: list[str] = []
    current = -1
    number = 0
    for step in trail.steps:
        if step.round != current:
            if current != -1:
                out.append("</ol></section>")
            current = step.round
            title = (
                "The dispute"
                if current == 0
                else f"Round {current}: {trail.round_titles.get(current, 'Looked again')}"
            )
            out.append(
                f'<section class="tl-round"><p class="tl-head">{escape(title)}</p><ol class="tl">'
            )
        if not step.minor:
            number += 1
        out.append(_item(step, number, opened))
    if current != -1:
        out.append("</ol></section>")
    return "".join(out)


def _item(step: Step, number: int, opened: datetime) -> str:
    stamp = f"{step.at:%H:%M}" if step.at.date() == opened.date() else when(step.at)
    actor = ACTOR.get(step.actor, step.actor.value)
    side = f'<p class="tl-when">{escape(actor)} · {escape(stamp)}</p>'
    if step.minor:
        return (
            f'<li class="tl-item tl--minor"><div class="tl-main">'
            f'<p class="tl-title">{escape(step.title)}</p></div>{side}</li>'
        )
    tag = MARKS.get(step.tone)
    title = f"{number}. {escape(step.title)}" + (f" {chip(tag, step.tone)}" if tag else "")
    parts = [f'<p class="tl-title">{title}</p>']
    if step.summary:
        parts.append(f'<p class="tl-sum">{_text(step.summary)}</p>')
    said, findings = (step.quoted[0], step.quoted[1:]) if step.quoted else ("", ())
    if said:
        parts.append(
            '<p class="said-label">Its reasoning, exactly as recorded:</p>'
            f'<blockquote class="said">{_text(said)}</blockquote>'
        )
    shown = [line for line in step.lines if line]
    if shown or findings:
        more = "".join(f"<li>{_text(line)}</li>" for line in shown)
        found = "".join(f"<li>{_text(line)}</li>" for line in findings)
        parts.append(
            '<details class="tl-more"><summary>Details</summary>'
            + (f"<ul>{more}</ul>" if more else "")
            + (f'<p class="said-label">Findings, as recorded:</p><ul>{found}</ul>' if found else "")
            + "</details>"
        )
    return (
        f'<li class="tl-item tl--{step.tone.value}"><div class="tl-main">{"".join(parts)}</div>'
        f"{side}</li>"
    )
