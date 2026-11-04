"""The eval harness (P4-11): it reads the real catalogue, and its arithmetic is honest."""

from __future__ import annotations

from pathlib import Path

from dispute_agent.modules.evals.harness import (
    Report,
    Row,
    Scenario,
    load_catalogue,
    phase_number,
    run,
)

CATALOGUE = Path(__file__).resolve().parents[3] / "docs" / "spec" / "eval-scenarios.yaml"


def scenario(sid: str, expected: str | None, baseline: str | None = None) -> Scenario:
    return Scenario(
        id=sid, title="t", phase=4, fixture="scanner_fault", expected=expected, baseline=baseline
    )


class TestTheCatalogue:
    def test_every_scenario_is_read(self) -> None:
        scenarios = load_catalogue(CATALOGUE)
        assert len(scenarios) == 59
        s01 = next(s for s in scenarios if s.id == "S01")
        assert (s01.expected, s01.baseline, s01.fixture) == (
            "propose_credit",
            "propose_credit",  # "same" resolves to the first expected action
            "calc_error_wrong_slab",
        )

    def test_phase_labels_order_correctly(self) -> None:
        assert phase_number(4) < phase_number("4b") < phase_number(5)


class TestTheRunner:
    def test_it_reproduces_a_known_first_round(self) -> None:
        """Both columns come from the runner it is given, for the scenario's fixture."""
        seen: list[tuple[str, str]] = []

        def first_round(fixture: str, which: str) -> str | None:
            seen.append((fixture, which))
            return "request_seller_evidence"

        report = run([scenario("S99", "request_seller_evidence")], first_round)
        assert seen == [("scanner_fault", "baseline"), ("scanner_fault", "model")]
        assert report.rows[0].model_correct is True
        assert report.correct_action_rate() == 1.0

    def test_scenarios_beyond_the_phase_are_left_out_not_failed(self) -> None:
        later = Scenario(id="S7", title="t", phase=7, fixture="x", expected="wait", baseline=None)
        assert run([later], lambda f, w: "wait", max_phase=5).rows == []


class TestDenominators:
    def test_an_n_a_scenario_does_not_enter_the_action_rate(self) -> None:
        """The catalogue's own rule: n/a is excluded deliberately, not by accident."""
        report = Report(
            rows=[
                Row(scenario("S1", "propose_credit"), "propose_credit", "propose_credit"),
                Row(scenario("S2", None), None, "escalate_to_human"),
            ]
        )
        assert len(report.counted()) == 1
        assert report.correct_action_rate() == 1.0

    def test_a_regression_is_where_the_baseline_was_right_and_the_model_was_not(self) -> None:
        report = Report(
            rows=[
                Row(scenario("S1", "propose_credit"), "propose_credit", "uphold_charge"),
                Row(scenario("S2", "wait"), "escalate_to_human", "wait"),
            ]
        )
        assert [r.scenario.id for r in report.regressions()] == ["S1"]
        assert report.correct_action_rate() == 0.5
        assert report.baseline_rate() == 0.5
