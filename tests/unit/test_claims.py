"""P6-09: the README and the reports never make a claim the evidence does not support.

release-gates.yaml `claims_not_to_make`, as a test: the phrases are grepped for in every
document a reader would take a claim from - the README, the demo and module guides, and the
eval report as `make eval` renders it. A percentage must always carry its sample.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: The claims, as the phrases that would make them (case-insensitive).
FORBIDDEN = {
    "production-ready": "a synthetic pilot (I11)",
    "production ready": "a synthetic pilot (I11)",
    "fully autonomous": "every credit and, in the pilot, every message is approved (D6)",
    "injection-proof": "no injection in the corpus succeeded - not a guarantee (RR2)",
    "secure against prompt injection": "no injection in the corpus succeeded (RR2)",
    "hours saved": "nothing measures real operator time",
    "saves time": "nothing measures real operator time",
    "saves hours": "nothing measures real operator time",
    "verified delivery": "verified means in Sent Items with the approved text (ADR 0010)",
    "delhivery": "the pack models a public workflow with illustrative numbers (D2, D10)",
    "proves fault": "patterns are hypotheses that only add caution (I7)",
}

DOCUMENTS = ["README.md", "docs/DEMO.md", "docs/MODULES.md"]


def _report_text() -> str:
    """The report exactly as `make eval` renders it, for a run where everything matched."""
    from dispute_agent.modules.evals.scenarios.catalogue import load
    from dispute_agent.modules.evals.scenarios.checks import Outcome, Result
    from dispute_agent.modules.evals.scenarios.gates import evaluate, load_config
    from dispute_agent.modules.evals.scenarios.metrics import compute
    from dispute_agent.modules.evals.scenarios.report import render

    scenarios = [s for s in load(ROOT / "docs" / "spec" / "eval-scenarios.yaml") if s.runnable]
    results = [Result(s.id, Outcome(final=s.expect.get("final"))) for s in scenarios]
    measures = compute(scenarios, results)
    config = load_config(ROOT / "config" / "release_gates.yaml")
    return render(
        mode="recorded",
        scenarios=scenarios,
        results=results,
        measures=measures,
        gates=evaluate(config, measures, "recorded"),
    )


def texts() -> list[tuple[str, str]]:
    found = [(name, (ROOT / name).read_text(encoding="utf-8")) for name in DOCUMENTS]
    found.append(("rendered eval report", _report_text()))
    latest = ROOT / "data" / "eval" / "suite-report.md"
    if latest.is_file():
        found.append(("data/eval/suite-report.md", latest.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize(("name", "text"), texts(), ids=lambda v: v if len(v) < 40 else "")
def test_no_forbidden_claim(name: str, text: str) -> None:
    lowered = text.lower()
    hits = [f"{phrase!r} ({why})" for phrase, why in FORBIDDEN.items() if phrase in lowered]
    assert not hits, f"{name} claims: " + "; ".join(hits)


@pytest.mark.parametrize(("name", "text"), texts(), ids=lambda v: v if len(v) < 40 else "")
def test_every_percentage_carries_its_sample(name: str, text: str) -> None:
    """'Accurate' without the metric and the sample is a claim the gates forbid."""
    for line in text.splitlines():
        for _match in re.finditer(r"\b\d+(\.\d+)?%", line):
            assert re.search(r"\d+(\.\d+)?% (over|of) \d+", line), f"{name}: {line.strip()}"


def test_the_readme_states_its_limits() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    for must in ("synthetic", "do not show production readiness", "approval", "sent items"):
        assert must in readme, must
