"""Screen 2: the agent's steps for one case. Layout over `ui.trail` and `ui.journey`.

Read top to bottom the way a person asks: where is this case and what happens next (the
headline), how far along it is (five phases), then what happened, round by round, each step
with who did it and one line to take in at a glance. Details and the recorded reasoning fold
away under each step. The case lives in the URL (`?case=`); an open case refreshes itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.journey import Journey, Mark, journey
from dispute_agent.ui.labels import Tone
from dispute_agent.ui.seller import tickets
from dispute_agent.ui.theme import chip, escape, who
from dispute_agent.ui.trail import Step, Trail, build

if TYPE_CHECKING:
    from datetime import datetime

REFRESH_S = 4

#: The state word under each phase: words, so the bar never relies on colour alone.
MARK_WORD = {
    Mark.DONE: "done",
    Mark.CURRENT: "now",
    Mark.WAITING: "waiting",
    Mark.PERSON: "with a person",
    Mark.SKIPPED: "skipped",
    Mark.TODO: "later",
}
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
    case_id = st.selectbox(
        "Case",
        ids,
        index=ids.index(wanted) if wanted in ids else 0,
        format_func=lambda cid: next(
            f"{t.shipment_ref}, {t.label}, opened {t.opened_at:%d %b %H:%M}"
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
    _headline(trail, where, case.opened_at)
    _phases(where)
    st.divider()
    _rounds(trail)


def _headline(trail: Trail, where: Journey, opened: datetime) -> None:
    calls = (
        f"{trail.ai_calls} AI call{'s' if trail.ai_calls != 1 else ''}, USD {trail.ai_cost:.4f}"
        if trail.ai_calls
        else "no AI calls"
    )
    st.markdown(
        f"{chip(trail.label, trail.tone)}"
        f'<p class="headline">{escape(where.headline)}</p>'
        f'<p class="next"><b>Next:</b> {escape(where.next_step)}</p>'
        f'<p class="muted">Shipment {escape(trail.shipment_ref)}, opened '
        f"{opened:%d %b %H:%M}, {escape(calls)}"
        f"{', refreshing while open' if trail.is_open else ''}.</p>",
        unsafe_allow_html=True,
    )


def _phases(where: Journey) -> None:
    settled = where.tone is Tone.SETTLED
    pills = []
    for name, mark in where.phases:
        style = "settled" if settled and name == "Closed" else mark.value
        pills.append(
            f'<div class="phase phase--{style}"><b>{escape(name)}</b>'
            f"<small>{escape(MARK_WORD[mark])}</small></div>"
        )
    st.markdown(
        f'<div class="phases" role="list" aria-label="Progress">{"".join(pills)}</div>',
        unsafe_allow_html=True,
    )


def _rounds(trail: Trail) -> None:
    current = -1
    for number, step in enumerate(trail.steps, 1):
        if step.round != current:
            current = step.round
            title = (
                "The dispute"
                if current == 0
                else f"Round {current}: {trail.round_titles.get(current, 'Looked again')}"
            )
            st.markdown(f'<p class="round">{escape(title)}</p>', unsafe_allow_html=True)
        _step(number, step)


def _step(number: int, step: Step) -> None:
    left, right = st.columns([1, 6], gap="small")
    with left:
        st.markdown(
            f'{who(step.actor.value)}<br><span class="muted">{step.at:%d %b %H:%M:%S}</span>',
            unsafe_allow_html=True,
        )
    with right:
        tag = MARKS.get(step.tone)
        st.markdown(
            f"**{number}. {escape(step.title)}**"
            + (f" &nbsp; {chip(tag, step.tone)}" if tag else "")
            + (f'<br><span class="muted">{escape(step.summary)}</span>' if step.summary else ""),
            unsafe_allow_html=True,
        )
        shown = [escape(line) for line in step.lines if line]
        if shown or step.quoted:
            with st.expander("Details"):
                if shown:
                    st.markdown(
                        f'<p class="muted">{"<br>".join(shown)}</p>', unsafe_allow_html=True
                    )
                if step.quoted:
                    st.caption("Reasoning and findings, exactly as recorded:")
                    st.text("\n".join(step.quoted))
