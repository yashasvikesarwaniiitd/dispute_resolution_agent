"""Screen 5: Evals - the one product metric, Decision Quality, offline and online (ADR 0066).

Offline: the stored runs on the 18 decision points the owner approved (ADR 0064) - how often
the AI's choice was acceptable, the rules baseline beside it, and how far the judge agrees with
the owner's own labels. Read from the committed run records; nothing is run from here.

Online: the same judge on the AI's decisions on live cases, where there is no answer key. The
switch on this screen turns it on or off; off, nothing is judged and nothing is spent. The
judge's answer never moves a case: it is shown here, beside the decision, for a person to read.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dispute_agent.api.online_judge import CAP_USD, FILE, JUDGE_LOG, OnlineEvals, spent_usd
from dispute_agent.modules.evals.decision_quality import load_golden, load_runs, run_summary
from dispute_agent.ui import data
from dispute_agent.ui.theme import escape

SWITCH = "online-switch"
VIEW = "evals-view"


def page() -> None:
    st.title("Decision Quality")
    st.markdown(
        '<p class="muted">At each decision point, is the AI\'s choice one a careful reviewer '
        "would accept? Offline it is graded against the decision points you approved; online "
        "the calibrated judge checks each new decision on a live case.</p>",
        unsafe_allow_html=True,
    )
    view = st.segmented_control(
        "View", ["Offline", "Online"], default="Offline", key=VIEW, label_visibility="collapsed"
    )
    if view == "Online":
        _online(OnlineEvals(data.DATA / FILE))
    else:
        _offline()


# -- offline ---------------------------------------------------------------------------------


def _offline() -> None:
    records = load_runs()
    if not records:
        st.markdown('<p class="muted">No stored runs yet.</p>', unsafe_allow_html=True)
        return
    summaries = [run_summary(r) for r in records]
    ai = _latest(records, summaries, lambda s: s["kind"] == "decisions" and s["decider"] != "rules")
    rules = _latest(records, summaries, lambda s: s["decider"] == "rules")
    judge = _latest(records, summaries, lambda s: s["kind"] == "judge")

    cards = st.columns(3)
    if ai:
        _card(
            cards[0],
            "The AI's decisions",
            f"{ai[1]['acceptable']} of {ai[1]['n']}",
            f"acceptable; {ai[1]['prohibited']} prohibited, {ai[1]['not_acceptable']} not "
            f"acceptable. {ai[1]['model']}, {_day(ai[1]['created_at'])}.",
        )
    if rules:
        _card(
            cards[1],
            "Rules baseline",
            f"{rules[1]['acceptable']} of {rules[1]['n']}",
            f"acceptable; {rules[1]['prohibited']} prohibited. The fixed rules table on the "
            "same points, for comparison.",
        )
    if judge:
        _card(
            cards[2],
            "Judge agrees with you",
            f"{judge[1]['agree']} of {judge[1]['n']}",
            f"labels; {judge[1]['critical_caught']} of {judge[1]['critical']} critical flaws "
            f"caught, {judge[1]['misses']} missed, {judge[1]['false_alarms']} false alarms.",
        )

    if ai:
        misses = [p for p in ai[0].get("points", []) if p.get("grade") not in (None, "acceptable")]
        with st.container(key="card-dq-misses"):
            st.header(f"Where the AI's decision was not acceptable ({len(misses)})")
            if misses:
                golden = load_golden()
                st.table(
                    [
                        {
                            "Point": p["id"],
                            "Shipment": p.get("shipment"),
                            "Situation": golden.point(p["id"]).situation,
                            "AI chose": _words(p.get("action")),
                            "Grade": _words(p.get("grade")),
                            "Acceptable here": ", ".join(
                                _words(a) for a in golden.point(p["id"]).acceptable
                            ),
                        }
                        for p in misses
                    ],
                    hide_index=True,
                    border="horizontal",
                )
            else:
                st.caption("Every decision point was acceptable.")

    with st.container(key="card-dq-runs"):
        st.header(f"Every run ({len(summaries)})")
        st.table([_run_row(s) for s in reversed(summaries)], hide_index=True, border="horizontal")
        st.caption(
            "Runs are stored with the code that produced them. Two decision runs compare only "
            "when they were graded on the same decision points by the same grader."
        )


def _latest(
    records: list[dict[str, Any]], summaries: list[dict[str, Any]], wanted: Any
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    found = [(r, s) for r, s in zip(records, summaries, strict=True) if wanted(s)]
    return found[-1] if found else None


def _run_row(s: dict[str, Any]) -> dict[str, Any]:
    judge = s["kind"] == "judge"
    return {
        "When": _day(s["created_at"]),
        "What ran": "Judge vs your labels" if judge else (s["decider"] or "").title(),
        "Model": s["model"] or "none (rules)",
        "Prompt": s["prompt_version"] or "",
        "Items": s["n"],
        "Result": (
            f"{s['agree']} agree, {s['critical_caught']}/{s['critical']} critical caught"
            if judge
            else f"{s['acceptable']} acceptable, {s['prohibited']} prohibited"
        ),
        "Cost (USD)": s["cost_usd"],
        "Code": s["git_commit"],
    }


# -- online ----------------------------------------------------------------------------------


def _online(store: OnlineEvals) -> None:
    on = store.is_on()
    with st.container(key="card-online-switch"):
        wanted = st.toggle("Online evaluation", value=on, key=SWITCH)
        if wanted != on:
            store.set_on(wanted)
            on = wanted
        spent = spent_usd(data.DATA / JUDGE_LOG)
        st.caption(
            (
                "On: each new decision the AI makes on a live case is checked by the judge, "
                "one every few seconds, about USD 0.001 each. "
                if on
                else "Off: nothing is judged and nothing is spent. Decisions made while it is "
                "off are not recorded. "
            )
            + f"Spent USD {spent:.4f} of the USD {CAP_USD} cap; at the cap it switches itself off. "
            "The judge's answer never changes a case."
        )

    rows = store.rows()
    judged = [r for r in rows if r["status"] == "judged"]
    flagged = [r for r in judged if r["verdict"] == "fail"]
    counts = st.columns(4)
    _card(counts[0], "Judged", str(len(judged)), "live decisions checked")
    _card(counts[1], "Holds up", str(len(judged) - len(flagged)), "explanation supported")
    _card(counts[2], "Flagged", str(len(flagged)), "for a person to look at")
    waiting = sum(1 for r in rows if r["status"] == "pending")
    failed = sum(1 for r in rows if r["status"] == "error")
    _card(counts[3], "Waiting", str(waiting), f"{failed} could not be judged")

    with st.container(key="card-online-rows"):
        st.header(f"Live decisions ({len(rows)})")
        if not rows:
            st.markdown(
                '<p class="muted">No live decision has been recorded yet. Turn online '
                "evaluation on, then raise a dispute: the AI's decision appears here with the "
                "judge's reading of it.</p>",
                unsafe_allow_html=True,
            )
            return
        st.table([_live_row(r) for r in rows], hide_index=True, border="horizontal")


def _live_row(r: dict[str, Any]) -> dict[str, Any]:
    j = r["judgement"] or {}
    why = [f"{c['claim']}: {c['why']}" for c in j.get("unsupported_claims", [])]
    if j.get("suspicion_as_fact"):
        why.append(f"suspicion stated as fact: {j.get('suspicion_detail', '')}")
    if j and not j.get("action_reasonable", True):
        why.append(f"action questioned: {j.get('action_note', '')}")
    verdict = {"pass": "Holds up", "fail": "Flagged"}.get(r["verdict"] or "", "")
    return {
        "When": _day(r["created_at"]),
        "Case": r["case_id"],
        "Round": r["round"],
        "AI chose": _words(r["action"]),
        "Judge": verdict or ("Waiting" if r["status"] == "pending" else "Could not judge"),
        "Why": "; ".join(why) or (r["error"] or ""),
        "AI's explanation": r["rationale"],
    }


# -- shared ----------------------------------------------------------------------------------


def _card(where: Any, label: str, figure: str, note: str) -> None:
    with where, st.container(border=True):
        st.markdown(
            f'<p class="muted">{escape(label)}</p><p class="figure-lg">{escape(figure)}</p>'
            f'<p class="muted">{escape(note)}</p>',
            unsafe_allow_html=True,
        )


def _words(code: str | None) -> str:
    return (code or "").replace("_", " ").capitalize()


def _day(iso: str | None) -> str:
    return (iso or "")[:16].replace("T", " ")
