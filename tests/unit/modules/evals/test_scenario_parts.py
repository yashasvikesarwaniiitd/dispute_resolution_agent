"""The simulator's pure parts: reading scripts, and checking a run against its declaration."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from dispute_agent.modules.evals.scenarios.catalogue import load, recorded_actions
from dispute_agent.modules.evals.scenarios.checks import Outcome, check

CATALOGUE = Path(__file__).resolve().parents[4] / "docs" / "spec" / "eval-scenarios.yaml"
BY_ID = {s.id: s for s in load(CATALOGUE)}
H = timedelta(hours=1)


class TestReadingScripts:
    def test_an_included_script_runs_first_and_offsets_count_from_its_end(self) -> None:
        steps = BY_ID["S09a"].steps
        assert [s.name for s in steps[:5]] == [
            "dispute_raised",
            "approve",
            "timer",
            "approve",
            "timer",
        ]
        assert steps[5].at == 73 * H + timedelta(days=5)  # (73h + 5d)
        assert steps[6].at == steps[5].at + H  # +1h

    def test_a_step_split_by_yaml_commas_is_joined_back(self) -> None:
        last = BY_ID["S24"].steps[-1]
        assert (last.name, last.argument) == ("dispute_raised", "new id, same shipment and claim")

    def test_a_duplicate_is_marked(self) -> None:
        assert [s.duplicate for s in BY_ID["S11"].steps if s.name == "approve"] == [
            False,
            True,
            False,
        ]

    def test_arguments_are_read_as_pairs(self) -> None:
        [took] = [s for s in BY_ID["S21"].steps if s.name == "human_took_case"]
        assert took.args() == {"notify_seller": "true"}

    def test_recorded_answers_include_the_included_rounds(self) -> None:
        assert recorded_actions(BY_ID["S09a"], BY_ID) == [
            "request_seller_evidence",
            "uphold_charge",
        ]

    def test_an_implied_first_request_is_recorded(self) -> None:
        """S14a lists only what follows the reply; its script approves a request first."""
        assert recorded_actions(BY_ID["S14a"], BY_ID) == [
            "request_seller_evidence",
            "escalate_to_human",
        ]


class TestChecking:
    def test_a_scenario_declaring_nothing_fails_as_under_specified(self) -> None:
        """The gate must never pass by skipping a row (release-gates.yaml)."""
        result = check("SX", {"actions": "n/a"}, Outcome(final="RECEIVED"))
        assert not result.passed
        assert "under-specified" in result.mismatches[0]

    def test_ending_with_a_person_when_that_is_expected_passes(self) -> None:
        result = check(
            "SX",
            {"final": "NEEDS_HUMAN_REVIEW", "review": "ops_timeout"},
            Outcome(final="NEEDS_HUMAN_REVIEW", review="ops_timeout"),
        )
        assert result.passed

    def test_every_declared_field_is_compared(self) -> None:
        result = check(
            "SX",
            {"path_contains": ["A", "C"], "final": "C", "counts": {"emails_sent": 1}},
            Outcome(path=["A", "B", "C"], final="C", counts={"emails_sent": 2}),
        )
        assert result.mismatches == ["count emails_sent = 2, expected 1"]

    def test_an_at_least_count(self) -> None:
        expect = {"counts": {"model_route_failovers": ">=1"}}
        assert check("SX", expect, Outcome(counts={"model_route_failovers": 2})).passed
        assert not check("SX", expect, Outcome(counts={"model_route_failovers": 0})).passed

    def test_a_crashed_run_is_a_failure_not_a_skip(self) -> None:
        assert not check("SX", {"final": "X"}, Outcome(error="RuntimeError: boom")).passed
