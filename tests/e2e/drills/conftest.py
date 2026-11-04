"""Drills write their setups as tiny scenarios in the catalogue's own script language.

So a drill exercises the same simulator as the suite: the same steps, recorded answers and
fakes. `stage(...)` plays a script and returns the live world, for the drill to attack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml

from dispute_agent.api import scenario_runner
from dispute_agent.app import build_scenario_models
from dispute_agent.modules.evals.scenarios.catalogue import load, recorded_actions

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def scenario(tmp: Path, fixture: str, script: list[str], actions: list[str], **extra: Any) -> Any:
    entry = {
        "id": "D1",
        "title": "drill",
        "phase": 5,
        "fixture": fixture,
        "script": script,
        "expect": {"actions": actions, "final": "RECEIVED"},
        **extra,
    }
    path = tmp / "drill.yaml"
    path.write_text(yaml.safe_dump({"version": 1, "scenarios": [entry]}), encoding="utf-8")
    [built] = load(path)
    return built


@pytest.fixture
def stage(tmp_path: Path) -> Iterator[Any]:
    played: list[Any] = []

    def run(fixture: str, script: list[str], actions: list[str], **extra: Any) -> Any:
        built = scenario(tmp_path, fixture, script, actions, **extra)
        result = scenario_runner._run(
            built, recorded_actions(built, {built.id: built}), build_scenario_models
        )
        played.append(result)
        return result

    yield run
    for result in played:
        result.world.close()
