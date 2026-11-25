"""The Evals screen (ADR 0066): Decision Quality offline and online, and the online switch."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from streamlit.testing.v1 import AppTest

from dispute_agent.api.online_judge import FILE, OnlineEvals

if TYPE_CHECKING:
    from pathlib import Path


def _evals() -> None:
    from dispute_agent.ui.screens import evals

    evals.page()


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    parts += [t.value.to_csv() for t in at.table]  # every cell, not pandas' shortened print
    return "\n".join(str(p) for p in parts)


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)  # the screen reads ./data, as the console does
    (tmp_path / "data").mkdir()
    return tmp_path / "data"


def test_offline_shows_the_stored_runs(data: Path) -> None:
    at = AppTest.from_function(_evals, default_timeout=30).run()
    shown = _text(at)
    assert "16 of 18" in shown and "17 of 18" in shown and "25 of 28" in shown
    assert "DQ-10" in shown and "DQ-17" in shown  # the two the AI got wrong


def test_the_switch_turns_online_evaluation_on_and_off(data: Path) -> None:
    store = OnlineEvals(data / FILE)
    at = AppTest.from_function(_evals, default_timeout=30).run()
    at.segmented_control(key="evals-view").set_value("Online").run()
    assert not store.is_on() and "nothing is spent" in _text(at)
    at.toggle(key="online-switch").set_value(True).run()
    assert store.is_on()
    at.toggle(key="online-switch").set_value(False).run()
    assert not store.is_on()


def test_a_flagged_live_decision_is_listed_with_why(data: Path) -> None:
    from dispute_agent.api.online_judge import JudgedReasoner
    from dispute_agent.modules.reasoning.llm.judge import evidence_lines
    from tests.unit.api.test_online_judge import BRIEFING, _AsLlm

    store = OnlineEvals(data / FILE)
    store.set_on(True)
    proposal = JudgedReasoner(_AsLlm(), store, evidence_lines).propose(BRIEFING)
    store.finish(
        str(proposal.id),
        judgement={
            "unsupported_claims": [{"claim": "a photo was sent", "why": "no photo in evidence"}],
            "suspicion_as_fact": False,
            "action_reasonable": True,
            "verdict": "fail",
        },
    )
    at = AppTest.from_function(_evals, default_timeout=30).run()
    at.segmented_control(key="evals-view").set_value("Online").run()
    shown = _text(at)
    assert "Flagged" in shown and "a photo was sent: no photo in evidence" in shown
