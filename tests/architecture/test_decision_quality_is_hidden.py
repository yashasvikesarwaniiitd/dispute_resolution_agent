"""The answer key never reaches the agent (ADR 0064).

Only the grader and the replay may know Decision Quality exists. If anything on the agent's
path - the kernel, a pack, the reasoning module, a prompt - could read the golden file, the
agent could be tuned to the test rather than graded by it.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "dispute_agent"
ALLOWED = {
    SRC / "modules" / "evals" / "decision_quality.py",
    SRC / "api" / "decision_quality.py",
    # The Evals screen shows the stored results (ADR 0066). It is not on the agent's path.
    SRC / "ui" / "screens" / "evals.py",
}
NAMES = ("decision_quality", "golden_v1")


def test_only_the_grader_and_the_replay_mention_the_answer_key() -> None:
    found = [
        str(path.relative_to(ROOT))
        for path in SRC.rglob("*.py")
        if path not in ALLOWED and any(n in path.read_text(encoding="utf-8") for n in NAMES)
    ]
    assert found == []


def test_no_prompt_mentions_the_answer_key() -> None:
    prompts = [p for p in (ROOT / "prompts").rglob("*") if p.is_file()]
    assert prompts, "the prompts folder moved; point this test at it"
    found = [
        str(p.relative_to(ROOT))
        for p in prompts
        if any(n in p.read_text(encoding="utf-8", errors="ignore") for n in NAMES)
    ]
    assert found == []
