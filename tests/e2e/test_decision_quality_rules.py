"""The rules baseline on the golden set, replayed offline (ADR 0064).

This locks today's baseline: if a change to the rules changes a decision, this test names it.
It does not say the rules are right - the grades do. DQ-14 is the known gap: on a hub answer
code cannot read, the rules wait as the model did live on 2026-11-25, and the owner's answer
key prohibits waiting there.
"""

from __future__ import annotations

import pytest

from dispute_agent.api.decision_quality import rules_only, run
from dispute_agent.app import build_scenario_models

EXPECTED = {
    "DQ-01": ("propose_credit", "acceptable"),
    "DQ-02": ("propose_credit", "acceptable"),
    "DQ-03": ("request_remeasurement", "acceptable"),
    "DQ-04": ("uphold_charge", "acceptable"),
    "DQ-05": ("uphold_charge", "acceptable"),
    "DQ-06": ("uphold_charge", "acceptable"),
    "DQ-07": ("uphold_charge", "acceptable"),
    "DQ-08": ("uphold_charge", "acceptable"),
    "DQ-09": ("request_seller_evidence", "acceptable"),
    "DQ-10": ("request_seller_evidence", "acceptable"),
    "DQ-11": ("propose_credit", "acceptable"),
    "DQ-12": ("uphold_charge", "acceptable"),
    "DQ-13": ("propose_credit", "acceptable"),
    "DQ-14": ("wait", "prohibited"),
    "DQ-15": ("wait", "acceptable"),
    "DQ-16": ("escalate_to_human", "acceptable"),
    "DQ-17": ("request_remeasurement", "acceptable"),
    "DQ-18": ("escalate_to_human", "acceptable"),
}


@pytest.fixture(scope="module")
def record() -> dict[str, object]:
    return run(models=rules_only(build_scenario_models))


def test_every_decision_point_is_reached_and_graded(record: dict[str, object]) -> None:
    assert record["not_reached"] == []
    found = {p["id"]: (p["action"], p["grade"]) for p in record["points"]}  # type: ignore[attr-defined]
    assert found == EXPECTED


def test_credits_are_code_s_amounts_for_the_proven_errors(record: dict[str, object]) -> None:
    proposed = {
        p["id"]: (p["reason_code"], p["amount_inr"])
        for p in record["points"]  # type: ignore[attr-defined]
        if p["action"] == "propose_credit"
    }
    assert proposed == {
        "DQ-01": ("duplicate_charge", "152.00"),
        "DQ-02": ("calculation_error", "38.00"),
        "DQ-11": ("remeasurement_lower", "152.00"),
        "DQ-13": ("remeasurement_lower", "152.00"),
    }


def test_the_run_records_what_graded_it(record: dict[str, object]) -> None:
    assert record["decider"] == "rules" and record["cost_usd"] == "0"
    assert record["golden_version"] == 1 and len(str(record["golden_digest"])) == 64
    assert record["grader_version"] == 1 and record["git_commit"]
    assert all(p["judge"] is None for p in record["points"])  # type: ignore[attr-defined]
