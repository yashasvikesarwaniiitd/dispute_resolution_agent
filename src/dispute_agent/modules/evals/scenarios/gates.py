"""Release gates: thresholds from config, measures from the suite, fail closed (P6-03).

A gate fails when its metric breaches the threshold, when its threshold is null, when its metric
could not be measured, or when it names a metric nobody computes. "Fail closed" is the point:
a gate can never pass because something was missing.

Each gate is enforced by the run that can measure it (`measured_by` in the config): the
recorded suite for the system's behaviour, the live run for the model's choices.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

    from dispute_agent.modules.evals.scenarios.metrics import Measure

GROUPS = ("hard_gates", "quality_gates", "cost_and_time_gates")


@dataclass(frozen=True)
class GateResult:
    name: str
    group: str
    rule: str
    measure: Measure | None
    passed: bool
    why_failed: str = ""


def load_config(path: Path) -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return config


def evaluate(config: dict[str, Any], measures: dict[str, Measure], mode: str) -> list[GateResult]:
    """The gates `mode` ("recorded" or "live") enforces, each judged against its measure."""
    enforced = set(config.get("measured_by", {}).get(mode, []))
    results: list[GateResult] = []
    for group in GROUPS:
        for name, gate in (config.get(group) or {}).items():
            if name not in enforced:
                continue
            results.append(_judge(name, group, gate if isinstance(gate, dict) else {}, measures))
    return results


def _judge(name: str, group: str, gate: dict[str, Any], measures: dict[str, Measure]) -> GateResult:
    bound = next(
        ((k, gate[k]) for k in ("max", "min", "max_scenarios_worse") if k in gate), (None, None)
    )
    kind, threshold = bound
    rule = f"{kind} {threshold}" if kind else "no threshold"
    measure = measures.get(name)
    if kind is None or threshold is None:
        return GateResult(name, group, rule, measure, False, "threshold is null")
    if measure is None:
        return GateResult(name, group, rule, None, False, "no metric computes this gate")
    if measure.value is None:
        return GateResult(name, group, rule, measure, False, "nothing was measured")
    value, limit = measure.value, float(threshold)
    passed = value >= limit if kind == "min" else value <= limit
    return GateResult(
        name, group, rule, measure, passed, "" if passed else f"{value:.4g} breaches {rule}"
    )


def passed(results: list[GateResult]) -> bool:
    return bool(results) and all(r.passed for r in results)


__all__ = ["GateResult", "evaluate", "load_config", "passed"]
