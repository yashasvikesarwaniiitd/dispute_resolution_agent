"""P6-01: every non-Phase-7 scenario in the catalogue, played on the whole line.

Recorded model answers, fakes, the simulated ledger and a fake clock: deterministic, so the
release gate `recorded_fixture_suite_pass_rate` (min 1.0) is exactly this test. A scenario is
checked on every field it declares; one that declares nothing fails as under-specified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dispute_agent.api.scenario_runner import play
from dispute_agent.app import build_scenario_models
from dispute_agent.modules.evals.scenarios.catalogue import Scenario, load, recorded_actions

CATALOGUE = Path(__file__).resolve().parents[2] / "docs" / "spec" / "eval-scenarios.yaml"
ALL = load(CATALOGUE)
BY_ID = {s.id: s for s in ALL}
RUNNABLE = [s for s in ALL if s.runnable]

#: Scenarios whose catalogue entry is waiting on an owner decision ("stop and ask"). Strict:
#: the day the decision lands and the entry is fixed, the xfail fails and must be removed.
AWAITING_OWNER: dict[str, str] = {}


def test_the_suite_covers_every_scenario_but_phase_7() -> None:
    assert len(RUNNABLE) == 57
    assert {s.id for s in ALL} - {s.id for s in RUNNABLE} == {"S15", "S16"}


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            s,
            id=s.id,
            marks=[pytest.mark.xfail(reason=AWAITING_OWNER[s.id], strict=True)]
            if s.id in AWAITING_OWNER
            else [],
        )
        for s in RUNNABLE
    ],
)
def test_scenario(scenario: Scenario) -> None:
    result = play(scenario, recorded_actions(scenario, BY_ID), build_scenario_models)
    assert result.passed, f"{scenario.id} {scenario.title}: " + "; ".join(result.mismatches)
