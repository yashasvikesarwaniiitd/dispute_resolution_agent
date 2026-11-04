"""P6-02/03/07: gates fail closed, every gate has a metric, the report shows its samples."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dispute_agent.modules.evals.scenarios.catalogue import load
from dispute_agent.modules.evals.scenarios.checks import Outcome, Result
from dispute_agent.modules.evals.scenarios.gates import evaluate, load_config, passed
from dispute_agent.modules.evals.scenarios.metrics import Measure, compute
from dispute_agent.modules.evals.scenarios.report import render

ROOT = Path(__file__).resolve().parents[4]
CONFIG = load_config(ROOT / "config" / "release_gates.yaml")
SCENARIOS = [s for s in load(ROOT / "docs" / "spec" / "eval-scenarios.yaml") if s.runnable]
GROUPS = ("hard_gates", "quality_gates", "cost_and_time_gates")


def config_with(group: str, name: str, gate: dict[str, object], mode: str = "recorded") -> dict:  # type: ignore[type-arg]
    return {group: {name: gate}, "measured_by": {mode: [name]}}


class TestGatesFailClosed:
    def test_a_null_threshold_fails(self) -> None:
        results = evaluate(
            config_with("quality_gates", "g", {"min": None}),
            {"g": Measure(1.0, 10, "x")},
            "recorded",
        )
        assert not passed(results)
        assert results[0].why_failed == "threshold is null"

    def test_an_unmeasured_metric_fails(self) -> None:
        results = evaluate(
            config_with("quality_gates", "g", {"min": 0.5}),
            {"g": Measure(None, 0, "x")},
            "recorded",
        )
        assert not passed(results)

    def test_a_gate_nobody_computes_fails(self) -> None:
        results = evaluate(config_with("quality_gates", "g", {"min": 0.5}), {}, "recorded")
        assert not passed(results)

    def test_one_hard_breach_fails_whatever_else_passes(self) -> None:
        config = {
            "hard_gates": {"h": {"max": 0}},
            "quality_gates": {"q": {"min": 0.5}},
            "measured_by": {"recorded": ["h", "q"]},
        }
        measures = {"h": Measure(1.0, 57, "x"), "q": Measure(1.0, 57, "x")}
        assert not passed(evaluate(config, measures, "recorded"))

    def test_no_gate_enforced_is_not_a_pass(self) -> None:
        assert not passed([])

    def test_a_gate_belongs_to_the_run_that_can_measure_it(self) -> None:
        config = config_with("quality_gates", "g", {"min": 0.9}, mode="live")
        assert evaluate(config, {"g": Measure(0.1, 5, "x")}, "recorded") == []


class TestEveryGateIsDefined:
    def test_every_configured_gate_names_a_metric_that_is_computed(self) -> None:
        results = [Result(s.id, Outcome(final=s.expect.get("final"))) for s in SCENARIOS]
        computed = set(compute(SCENARIOS, results))
        configured = {name for g in GROUPS for name in CONFIG[g]}
        assert configured <= computed, configured - computed

    def test_every_gate_is_measured_by_exactly_one_run(self) -> None:
        recorded = set(CONFIG["measured_by"]["recorded"])
        live = set(CONFIG["measured_by"]["live"])
        assert not recorded & live
        assert {name for g in GROUPS for name in CONFIG[g]} == recorded | live


class TestTheReport:
    def _report(self) -> str:
        results = [Result(s.id, Outcome(final=s.expect.get("final"))) for s in SCENARIOS]
        measures = compute(SCENARIOS, results)
        return render(
            mode="recorded",
            scenarios=SCENARIOS,
            results=results,
            measures=measures,
            gates=evaluate(CONFIG, measures, "recorded"),
        )

    def test_it_lists_every_scenario(self) -> None:
        text = self._report()
        for scenario in SCENARIOS:
            assert f"| {scenario.id} |" in text

    def test_every_percentage_carries_its_denominator(self) -> None:
        """release-gates.yaml: never quote a rate without its sample."""
        for line in self._report().splitlines():
            for _match in re.finditer(r"\d+%", line):
                assert re.search(r"\d+% over \d+ ", line), line

    def test_it_opens_with_what_it_does_not_show(self) -> None:
        assert "do not show production readiness" in self._report()


@pytest.mark.parametrize("name", ["unauthorised_financial_actions", "duplicate_side_effects"])
def test_the_must_be_zero_gates_are_hard_and_zero(name: str) -> None:
    assert CONFIG["hard_gates"][name]["max"] == 0
