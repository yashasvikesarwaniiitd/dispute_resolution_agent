"""Ask the AI about one shipment: the Case Explainer on the Review screen (ADR 0058).

The chat is pinned to the selected case (`only_case`), so every question reads that one
shipment's record and nothing else. It explains; it cannot approve, send or change anything,
and nothing it says is acted on (ADR 0039). Answers are shown as escaped plain text (I5).
"""

from __future__ import annotations

import importlib.util
import logging
from typing import TYPE_CHECKING, Any, Literal

import streamlit as st

from dispute_agent.infra.settings import Settings
from dispute_agent.ui.theme import actor, escape, text

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.ui.seller import Ticket

log = logging.getLogger(__name__)

Tab = Literal["waiting", "person"]

#: One click to the questions a reviewer actually has, by tab.
STARTERS: dict[Tab, tuple[str, ...]] = {
    "waiting": (
        "Why does this need my approval?",
        "Where does this amount come from?",
        "What happens if I approve?",
    ),
    "person": (
        "Why did this come to a person?",
        "What evidence is missing?",
        "What have the seller and the hub said?",
    ),
}
#: The explainer's tools, as a reviewer would name what was read.
SOURCES = {
    "case_overview": "Case overview",
    "case_timeline": "Timeline",
    "case_decisions": "Decisions",
    "policy_lookup": "Policy rules",
}
#: shortcut: the explainer's lifetime cap counts spend in a file that Streamlit Cloud wipes on
#: restart, so this per-session limit bounds what one visitor can spend. Upgrade to a stored
#: counter if the site is opened beyond the password.
ASK_LIMIT = 10
#: Earlier exchanges sent with a question, so "and why?" has something to follow.
KEEP = 2
HISTORY = "ask_history"  # case_id -> [{"q", "a", "read"}]
#: The explainer call `(case_id, question) -> Answer`, handed in by the entry point.
EXPLAIN = "explain"
ASKED = "ask_count"


def prompt(case_id: str, ref: str, earlier: Sequence[dict[str, Any]], question: str) -> str:
    """The question as the explainer reads it: the pinned case, recent turns, the question."""
    lines = [f"Case {case_id} (shipment {ref})."]
    if earlier:
        lines.append("Earlier in this conversation:")
        for turn in earlier[-KEEP:]:
            lines += [f"Reviewer: {turn['q']}", f"You: {turn['a']}"]
    lines.append(f"Question: {question.strip()}")
    return "\n".join(lines)


def sources(tools_used: Sequence[str]) -> list[str]:
    """What was read, once each, in the order it was read ("mcp__case__case_timeline")."""
    named = [SOURCES.get(t.rpartition("__")[2], "") for t in tools_used]
    return [n for i, n in enumerate(named) if n and n not in named[:i]]


def unavailable() -> str | None:
    """Why the assistant cannot answer on this site, or None when it can."""
    if st.session_state.get(EXPLAIN) is None:
        return "This console was started without it (use make run-console)."
    if importlib.util.find_spec("claude_agent_sdk") is None:
        return "Its library is not installed on this site."
    settings = Settings()
    if settings.anthropic_api_key is None or not settings.anthropic_model_haiku:
        return "No Anthropic key or Haiku model is configured."
    return None


def _ask(case_id: str, question: str) -> Any:
    return st.session_state[EXPLAIN](case_id, question)


def _turn_html(turn: dict[str, Any]) -> str:
    read = turn.get("read") or []
    seen = f'<p class="ask-read">Read: {escape(" · ".join(read))}</p>' if read else ""
    return (
        f'<div class="ask-q"><p>{text(turn["q"])}</p></div>'
        f'<div class="ask-a">{actor("AI")}<p>{text(turn["a"])}</p>{seen}</div>'
    )


def panel(ticket: Ticket, tab: Tab) -> None:
    """The assistant's card for one shipment: suggested questions, the chat, and its limits."""
    case_id, ref = ticket.case_id, ticket.shipment_ref
    turns: list[dict[str, Any]] = st.session_state.setdefault(HISTORY, {}).setdefault(case_id, [])
    asked = int(st.session_state.get(ASKED, 0))
    why_not = unavailable()
    spent = asked >= ASK_LIMIT
    with st.container(key=f"card-ask-{case_id}"):
        st.markdown(
            f'<div class="ask-head">{actor("AI")}<h2 class="ask-title">Ask about '
            f'<span class="mono">{escape(ref)}</span></h2></div>'
            '<p class="ask-note">It reads only this shipment\'s record and explains it, saying '
            "what it read. It cannot approve, send or change anything.</p>",
            unsafe_allow_html=True,
        )
        log_slot = st.empty()
        question: str | None = None
        left = [s for s in STARTERS[tab] if all(t["q"] != s for t in turns)]
        if left:
            with st.container(horizontal=True, gap="small", key=f"ask-chips-{case_id}"):
                for i, starter in enumerate(left):
                    if st.button(
                        starter, key=f"ask-chip-{case_id}-{i}", disabled=bool(why_not) or spent
                    ):
                        question = starter
        typed = st.chat_input(
            "Ask anything about this shipment",
            key=f"ask-input-{case_id}",
            max_chars=500,
            disabled=bool(why_not) or spent,
        )
        question = question or typed
        foot, clear = (
            st.columns([5, 1], vertical_alignment="center") if turns else (st.container(), None)
        )
        if why_not:
            note = f"The assistant is off here. {why_not}"
        elif spent:
            note = f"You have used all {ASK_LIMIT} questions for this session."
        else:
            note = (
                f"{ASK_LIMIT - asked} of {ASK_LIMIT} questions left this session · Claude Haiku · "
                "it can be wrong; the record is the source of truth."
            )
        foot.markdown(f'<p class="ask-note">{escape(note)}</p>', unsafe_allow_html=True)
        if clear is not None and clear.button("Clear", type="tertiary", key=f"ask-clear-{case_id}"):
            turns.clear()
            st.rerun()

        error = None
        if question and question.strip() and not why_not and not spent:
            with log_slot.container(key=f"ask-log-{case_id}-busy"):
                st.markdown(
                    "".join(_turn_html(t) for t in turns)
                    + f'<div class="ask-q"><p>{text(question)}</p></div>',
                    unsafe_allow_html=True,
                )
                with st.spinner("Reading this shipment's record…"):
                    error = _answer(case_id, ref, turns, question)
            if error is None:  # redraw at once: the count, the starters and the log all moved
                st.rerun()
        if turns or error:
            with log_slot.container(key=f"ask-log-{case_id}"):
                st.markdown("".join(_turn_html(t) for t in turns), unsafe_allow_html=True)
                if error:
                    st.markdown(
                        f'<p class="line line--error">{escape(error)}</p>', unsafe_allow_html=True
                    )


def _answer(case_id: str, ref: str, turns: list[dict[str, Any]], question: str) -> str | None:
    """Ask, and keep the turn. Returns what to tell the reviewer if it could not answer."""
    from dispute_agent.kernel.domain.errors import ConfigError
    from dispute_agent.modules.reasoning.explainer.agent import ExplainerBudgetError

    try:
        answer = _ask(case_id, prompt(case_id, ref, turns, question))
    except ExplainerBudgetError:
        return "The assistant has reached its spending limit, so it is off until it is raised."
    except ConfigError as refused:
        return f"The assistant cannot start: {refused}"
    except Exception as failed:  # a network or SDK failure must not take the Review screen down
        log.warning("explainer_failed", extra={"error": type(failed).__name__})
        return "The assistant could not answer just now. Try again in a moment."
    if not answer.text:
        return "The assistant returned no answer. Try asking another way."
    turns.append({"q": question.strip(), "a": answer.text, "read": sources(answer.tools_used)})
    st.session_state[ASKED] = int(st.session_state.get(ASKED, 0)) + 1
    return None


__all__ = ["ASK_LIMIT", "STARTERS", "panel", "prompt", "sources", "unavailable"]
