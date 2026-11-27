"""Investigation evaluation replays (ADR 0072): each case played offline, its decision graded.

Like Decision Quality's replays (api/decision_quality.py): the dispute is raised, the hub's or
the seller's answer arrives as the case's script says, the rules baseline decides the earlier
rounds and the engine under test decides only the graded round - so every arm reaches that point
with exactly the same evidence. Fakes only: no Gmail, no Jira, a simulated ledger, a fake clock.

The engine under test is the caller's: scripted (free; proves the plumbing and safeguards), or a
real model (paid; only with the owner's approval and a cap the spending guard enforces).
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from dispute_agent.api import scenario_runner as runner
from dispute_agent.api.decision_quality import at_round
from dispute_agent.modules.evals.investigation import Case, Graded, grade, load_cases, summary
from dispute_agent.modules.evals.scenarios.catalogue import Scenario, script

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

RUNS = "evals/investigation/runs"


@dataclasses.dataclass(frozen=True)
class Outcome:
    action: str | None
    reason_code: str | None
    rationale: str
    not_reached: str = ""
    findings: tuple[dict[str, Any], ...] = ()
    flags: tuple[str, ...] = ()


def replay(case: Case, models: Any) -> Outcome:
    """Play the case offline and read back the proposal of its graded round."""
    scenario = Scenario(
        id=case.id,
        title=case.situation,
        phase=5,
        fixture=case.fixture,
        config={},
        faults={},
        steps=script(list(case.script)),
        expect={},
    )
    world = runner._run(scenario, None, models).world
    try:
        with world.engine.connect() as c:
            rows = c.execute(
                text(
                    "select action, proposal_json from proposals "
                    "where gather_round = :r and reasoner != 'human' order by created_at"
                ),
                {"r": case.round},
            ).all()
    finally:
        world.close()
    if not rows:
        return Outcome(None, None, "", not_reached=f"round {case.round} not reached")
    action, body = rows[-1]
    parsed = json.loads(body)
    return Outcome(
        action=str(action),
        reason_code=(parsed.get("action_parameters") or {}).get("reason_code"),
        rationale=str(parsed.get("rationale", "")),
        findings=tuple(parsed.get("findings") or ()),
        flags=tuple(parsed.get("manipulation_flags") or ()),
    )


def run(
    models_for: Callable[[Case, list[dict[str, Any]]], Any],
    *,
    label: str,
    cases: list[Case] | None = None,
    vision: bool = False,
) -> dict[str, Any]:
    """Every case replayed and graded. `models_for(case, turns)` returns the scenario models
    factory for one case, recording the graded round's turns into `turns`. `vision`: the route
    can read the parcel photo; without it, a case that needs the photo is "not_evaluated"."""
    graded: list[Graded] = []
    rows: list[dict[str, Any]] = []
    for case in cases or load_cases().cases:
        turns: list[dict[str, Any]] = []
        outcome = replay(case, at_round(models_for(case, turns), case.round))
        result = grade(
            case,
            outcome.action,
            [t for t in turns if t.get("gather_round") == case.round],
            rationale=outcome.rationale,
            findings=list(outcome.findings),
            flags=list(outcome.flags),
            vision=vision,
        )
        graded.append(result)
        rows.append(
            {
                **result.model_dump(),
                "reason_code": outcome.reason_code,
                "not_reached": outcome.not_reached,
                "trajectory": [t.get("step") for t in turns],
                "cited": sorted(
                    {r for f in outcome.findings for r in f.get("evidence_refs") or ()}
                ),
                "flags": list(outcome.flags),
            }
        )
    return {
        "label": label,
        "ran_at": datetime.now(UTC).isoformat(),
        "commit": _commit(),
        "summary": summary(graded),
        "cases": rows,
    }


def save(record: dict[str, Any], root: Path) -> Path:
    folder = root / RUNS
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inv-{record['label']}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(record, indent=1, default=str) + "\n", encoding="utf-8")
    return path


def _commit() -> str:
    done = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
    )
    return done.stdout.strip() or "unknown"


__all__ = ["Outcome", "replay", "run", "save"]
