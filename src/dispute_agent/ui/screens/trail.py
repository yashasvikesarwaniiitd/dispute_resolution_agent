"""Screen 2: the agent's steps for one case. Layout over `ui.trail` (ADR 0044).

The case lives in the URL (`?case=`), so a step view can be shared and reloaded. While the
case is open the trail refreshes every few seconds; a closed case is still.
"""

from __future__ import annotations

import streamlit as st

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data
from dispute_agent.ui.labels import Tone
from dispute_agent.ui.seller import tickets
from dispute_agent.ui.theme import chip, escape, who
from dispute_agent.ui.trail import Trail, build

REFRESH_S = 4


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
        st.page_link(st.session_state["pages"]["seller"], label="Raise a ticket")
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
    st.fragment(_steps, run_every=REFRESH_S if is_open else None)(case_id)


def _steps(case_id: str) -> None:
    with data.reader() as work:
        case = work.cases.get(case_id)  # type: ignore[arg-type]
        trail = build(work, case, usage=data.ai_usage(case.id))
    _summary(trail)
    for number, step in enumerate(trail.steps, 1):
        _step(number, step)


def _summary(trail: Trail) -> None:
    calls = (
        f"{trail.ai_calls} AI call{'s' if trail.ai_calls != 1 else ''}, USD {trail.ai_cost:.4f}"
        if trail.ai_calls
        else "No AI calls"
    )
    st.markdown(
        f"{chip(trail.label, trail.tone)} &nbsp; shipment {escape(trail.shipment_ref)} "
        f'<span class="muted">&nbsp; {escape(calls)}'
        f"{'' if not trail.is_open else ', refreshing while open'}</span>",
        unsafe_allow_html=True,
    )
    st.divider()


def _step(number: int, step: object) -> None:
    from dispute_agent.ui.trail import Step

    assert isinstance(step, Step)
    left, right = st.columns([1, 5], gap="small")
    with left:
        st.markdown(
            f'{who(step.actor.value)}<br><span class="muted">{step.at:%d %b %H:%M:%S}</span>',
            unsafe_allow_html=True,
        )
    with right:
        tag = MARKS.get(step.tone)
        st.markdown(
            f"**{number}. {escape(step.title)}**"
            + (f" &nbsp; {chip(tag, step.tone)}" if tag else ""),
            unsafe_allow_html=True,
        )
        shown = [escape(line) for line in step.lines if line]
        if shown:
            st.markdown(f'<p class="muted">{"<br>".join(shown)}</p>', unsafe_allow_html=True)
        if step.quoted:
            st.caption("Reasoning and findings, exactly as recorded:")
            st.text("\n".join(step.quoted))


MARKS = {Tone.SETTLED: "Credited", Tone.ATTENTION: "Needs a person"}
