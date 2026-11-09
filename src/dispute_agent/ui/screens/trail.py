"""Screen 2: the agent's steps - the signature screen (design handoff 4, Screen 2; ADR 0048).

A case list on the left (searchable, the selected case in the URL). On the right, one card -
the case's name and facts, where it stands (headline, Why, Next, the way onward), how far
along it is (the stepper, in words), and code's figures when the case could be disputed -
then "What happened": a timeline by round, each step saying what happened, who did it and
when, with ids and codes folded into Details. Open cases refresh themselves every 4 s.

All markup is built from escaped values (`theme.escape`); recorded text is shown "exactly as
recorded" in a quote block, never rendered (I5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import streamlit as st

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui import data, picker
from dispute_agent.ui.journey import Journey, Mark, journey
from dispute_agent.ui.labels import Tone, clock, when
from dispute_agent.ui.photos import photo_for
from dispute_agent.ui.seller import tickets
from dispute_agent.ui.theme import actor, chip, escape, recorded
from dispute_agent.ui.trail import Actor, Step, Trail, build

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.models import Case

REFRESH_S = 4
#: The mark of a phase that did not happen: an en dash, not a number (HANDOFF 3.3).
DASH = "\u2013"
PHASE_WORD = {
    Mark.DONE: "done",
    Mark.CURRENT: "in progress",
    Mark.WAITING: "waiting",
    Mark.PERSON: "with a person",
    Mark.SKIPPED: "not needed",
    Mark.NOT_REACHED: "not reached",
    Mark.TODO: "later",
}
#: Who did a step, as a reader names them (HANDOFF 3.2).
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
    '<ul class="legend" aria-label="Who does what">'
    f"<li>{actor('Code')}fixed checks and calculations, no AI</li>"
    f"<li>{actor('Rules engine').replace('Rules engine', 'AI or rules engine')}proposes what to do</li>"
    f"<li>{actor('Reviewer')}a person who approves</li>"
    f"<li>{actor('System')}the service's own bookkeeping</li>"
    "<li>Times are UTC.</li></ul>"
)
ONWARD = {
    CaseState.AWAITING_APPROVAL: ("review", "Decide on the Review screen →"),
    CaseState.NEEDS_HUMAN_REVIEW: ("review", "Decide on the Review screen →"),
    CaseState.RESOLVED_CREDITED: ("ledger", "See the credit in the ledger →"),
}


def page() -> None:
    st.title("The agent's steps")
    if not data.has_store():
        st.info(data.NO_STORE)
        return
    with data.reader() as work:
        everyone = tickets(work.cases.list_in_states(list(CaseState)))
    if not everyone:
        st.markdown(
            '<p class="muted">No cases yet. Raise a ticket on the seller screen and its steps '
            "appear here.</p>",
            unsafe_allow_html=True,
        )
        pages = st.session_state.get("pages")
        if pages:
            st.page_link(pages["seller"], label="Raise a ticket")
        return

    ids = [t.case_id for t in everyone]
    wanted = st.query_params.get("case") or st.session_state.get("case")
    selected = wanted if wanted in ids else ids[0]
    side, main = st.columns([1, 3], gap="large")
    with side:
        st.markdown(
            f'<h2 class="side-head">Cases <span class="muted">({len(ids)})</span></h2>',
            unsafe_allow_html=True,
        )
        query = st.text_input(
            "Find a case by shipment or status",
            placeholder="Shipment or status",
            label_visibility="collapsed",
            key="case-search",
        )
        q = query.strip().lower()
        shown = [
            t for t in everyone if not q or q in t.shipment_ref.lower() or q in t.label.lower()
        ]
        clicked = picker.show(
            {
                "view": "cases",
                "selected": selected,
                "empty": "No case matches that. Try a shipment number such as 000002.",
                "rows": [
                    {
                        "id": t.case_id,
                        "title": t.shipment_ref,
                        "aside": f"opened {clock(t.opened_at)}",
                        "status": t.label,
                        "tone": t.tone.value,
                    }
                    for t in shown
                ],
            },
            key="cases",
        )
        if clicked["pick"] and clicked["pick"] in ids and clicked["pick"] != selected:
            # Redraw at once, so the list shows the new case as chosen.
            st.session_state["case"] = clicked["pick"]
            st.query_params["case"] = clicked["pick"]
            st.rerun()
    st.query_params["case"] = selected
    st.session_state["case"] = selected
    is_open = next(t.is_open for t in everyone if t.case_id == selected)
    with main:
        st.fragment(_case, run_every=REFRESH_S if is_open else None)(selected)


def _case(case_id: str) -> None:
    with data.reader() as work:
        case = work.cases.get(case_id)  # type: ignore[arg-type]
        trail = build(work, case, usage=data.ai_usage(case.id), policy=data.policy())
        where = journey(work, case)
    with st.container(key="card-case"):
        photo = photo_for(case.facts.shipment_ref)
        text, picture = st.columns([3, 1], gap="large") if photo else (st.container(), None)
        with text:
            st.markdown(_identity(case, trail) + _stands(where), unsafe_allow_html=True)
            _onward(case)
        if picture is not None and photo is not None:
            picture.image(str(photo), caption="Parcel photo", width="stretch")
        st.markdown(_stepper(where) + _figures(where), unsafe_allow_html=True)
    st.header("What happened")
    st.markdown(LEGEND, unsafe_allow_html=True)
    st.markdown(timeline(trail, case.opened_at), unsafe_allow_html=True)


def _identity(case: Case, trail: Trail) -> str:
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
    live = (
        f' · <span class="live" role="status" aria-live="polite">refreshes every {REFRESH_S} s</span>'
        if trail.is_open
        else ""
    )
    return (
        f'<div class="case-head"><h2 class="case-ref">{escape(trail.shipment_ref)}</h2>'
        f"{chip(trail.label, trail.tone)}</div>"
        f'<p class="case-facts">{escape(" · ".join(facts))}{live}</p>'
    )


def _stands(where: Journey) -> str:
    why = ""
    if where.why:
        code = f' <span class="code">({escape(where.why_code)})</span>' if where.why_code else ""
        why = f"<dt>Why</dt><dd>{escape(where.why)}{code}</dd>"
    return (
        f'<p class="headline">{escape(where.headline)}</p>'
        f'<dl class="why-next">{why}<dt>Next</dt><dd>{escape(where.next_step)}</dd></dl>'
    )


def _onward(case: Case) -> None:
    """The way onward, when the next move is made on another screen."""
    pages = st.session_state.get("pages")
    target = ONWARD.get(case.state)
    if pages and target and st.button(target[1], type="primary", key=f"onward-{case.id}"):
        st.switch_page(pages[target[0]])


def _stepper(where: Journey) -> str:
    settled = where.tone is Tone.SETTLED
    items = []
    marks = [mark for _name, mark in where.phases]
    for i, (name, mark) in enumerate(where.phases):
        style = "settled" if settled and name == "Closed" else mark.value
        done = mark is Mark.DONE
        glyph = "✓" if done else (DASH if mark in (Mark.SKIPPED, Mark.NOT_REACHED) else str(i + 1))
        after = marks[i + 1] if i + 1 < len(marks) else None
        solid = done and after is not None and after is Mark.DONE
        items.append(
            f'<li class="step--{style}"><div class="track"><span class="mark" aria-hidden="true">'
            f'{glyph}</span><span class="link{" link--solid" if solid else ""}" aria-hidden="true">'
            f"</span></div><div><b>{escape(name)}</b><small>{escape(PHASE_WORD[mark])}</small>"
            "</div></li>"
        )
    return (
        '<h3 class="sr-only">How far along</h3>'
        f'<ol class="stepper" aria-label="Progress">{"".join(items)}</ol>'
    )


def _figures(where: Journey) -> str:
    if not where.figures:
        return ""
    cells = "".join(
        f"<div{' class=lead' if label == where.lead else ''}><dt>{escape(label)}</dt>"
        f"<dd>{escape(value)}</dd></div>"
        for label, value in where.figures
    )
    note = f'<p class="muted note">{escape(where.figure_note)}</p>' if where.figure_note else ""
    return (
        '<div class="figures"><p class="muted">Code\'s figures · worked out in code, not by the '
        f"AI</p><dl>{cells}</dl>{note}</div>"
    )


def timeline(trail: Trail, opened: datetime) -> str:
    """Every step, by round, as one block of escaped markup."""
    out: list[str] = []
    current, number = -1, 0
    for step in trail.steps:
        if step.round != current:
            if current != -1:
                out.append("</ol></section>")
            current = step.round
            title = (
                "The dispute"
                if current == 0
                else f"Round {current} · {trail.round_titles.get(current, 'Looked again')}"
            )
            out.append(
                f'<section class="round"><h3 class="round-head">{escape(title)}</h3><ol class="steps">'
            )
        if not step.minor:
            number += 1
        out.append(_item(step, number, opened))
    if current != -1:
        out.append("</ol></section>")
    return "".join(out)


def _item(step: Step, number: int, opened: datetime) -> str:
    stamp = clock(step.at) if step.at.date() == opened.date() else when(step.at)
    who = ACTOR.get(step.actor, step.actor.value)
    rail = f'<div class="rail" aria-hidden="true"><span class="dot dot--{step.mark.value}"></span></div>'
    if step.minor:
        pending = step.mark.value == "pending"
        side = "" if pending else f"<span>{escape(who)} · {escape(stamp)}</span>"
        css = "step-minor step-minor--pending" if pending else "step-minor"
        return (
            f'<li>{rail}<div class="step-body step-body--minor"><p class="{css}">'
            f"<span>{escape(step.title)}</span>{side}</p></div></li>"
        )
    tag = f" {chip(step.tag, step.tone)}" if step.tag else ""
    parts = [
        f'<div class="step-top"><h4 class="step-title">{number}. {escape(step.title)}{tag}</h4>'
        f'<p class="step-who">{actor(who)}{escape(stamp)}</p></div>'
    ]
    if step.summary:
        parts.append(f'<p class="step-sum">{escape(step.summary)}</p>')
    said, findings = (step.quoted[0], step.quoted[1:]) if step.quoted else ("", ())
    if step.quote_caption and step.quoted:  # observations: all of them, together
        listed = "\n".join(f"- {q}" for q in step.quoted)
        parts.append(recorded(step.quote_caption, listed))
        said, findings = "", ()
    if said:
        parts.append(
            recorded(f"Its reasoning, exactly as recorded · proposed by {step.proposer}", said)
        )
    details = [*step.lines, *(f"As recorded: {f}" for f in findings)]
    if details:
        items = "".join(f"<li>{escape(line)}</li>" for line in details)
        parts.append(f'<details class="more"><summary>Details</summary><ul>{items}</ul></details>')
    return f'<li>{rail}<div class="step-body">{"".join(parts)}</div></li>'
