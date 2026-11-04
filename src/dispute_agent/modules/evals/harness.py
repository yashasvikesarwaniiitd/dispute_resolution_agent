"""The eval harness: scenario catalogue in, baseline-versus-model table out (P4-11).

Reads `docs/spec/eval-scenarios.yaml`, plays the **first investigate round** of each scenario
through a runner the caller supplies (the composition root wires `run_trial`; this module
imports nothing but the kernel), once with the rules-only baseline and once with the reasoner
under test, and compares both with what the catalogue expects.

Field semantics honoured (agent-design.md section 5, the catalogue's own header):

- `expect.actions: n/a` means no proposal is ever produced: the scenario is **excluded** from
  the correct-action denominator, deliberately.
- `expect.baseline` is compared on the first round's proposal action: `same` means equal to
  `expect.actions[0]`.
- A scenario whose fixture does not reach INVESTIGATING produces no action on either side.

Later rounds (replies, ops findings, approvals) need the full event script; the Phase 6 runner
plays those. This harness measures what Phase 4 can: the first decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    phase: float  # "4b" is 4.5: after 4, before 5
    fixture: str | None
    expected: str | None  # None = n/a: excluded from the denominator
    baseline: str | None


@dataclass(frozen=True)
class Row:
    scenario: Scenario
    baseline_action: str | None
    model_action: str | None

    @property
    def model_correct(self) -> bool | None:
        if self.scenario.expected is None:
            return None
        return self.model_action == self.scenario.expected

    @property
    def baseline_as_catalogued(self) -> bool | None:
        if self.scenario.baseline is None:
            return None
        return self.baseline_action == self.scenario.baseline


@dataclass
class Report:
    rows: list[Row] = field(default_factory=list)

    def counted(self) -> list[Row]:
        return [r for r in self.rows if r.model_correct is not None]

    def correct_action_rate(self) -> float:
        counted = self.counted()
        return sum(1 for r in counted if r.model_correct) / len(counted) if counted else 0.0

    def baseline_rate(self) -> float:
        counted = [r for r in self.counted() if r.baseline_action is not None]
        return (
            sum(1 for r in counted if r.baseline_action == r.scenario.expected) / len(counted)
            if counted
            else 0.0
        )

    def regressions(self) -> list[Row]:
        """Where the baseline was right and the model was not (no_regression_vs_baseline)."""
        return [
            r
            for r in self.counted()
            if r.baseline_action == r.scenario.expected and not r.model_correct
        ]

    def table(self) -> str:
        lines = [
            f"{'scenario':8} {'expected':24} {'baseline':24} {'model':24} ok",
            "-" * 86,
        ]
        for r in self.rows:
            mark = {True: "yes", False: "NO", None: "n/a"}[r.model_correct]
            lines.append(
                f"{r.scenario.id:8} {r.scenario.expected or 'n/a':24} "
                f"{r.baseline_action or '-':24} {r.model_action or '-':24} {mark}"
            )
        counted = len(self.counted())
        lines += [
            "-" * 86,
            f"correct_action_rate (model):    {self.correct_action_rate():.0%} of {counted}",
            f"correct_action_rate (baseline): {self.baseline_rate():.0%}",
            f"regressions vs baseline:        {len(self.regressions())}",
            f"excluded (actions n/a):         {len(self.rows) - counted}",
        ]
        return "\n".join(lines)


def phase_number(value: object) -> float:
    text = str(value).strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    return float(digits or 99) + (0.5 if text[len(digits) :] else 0.0)


def load_catalogue(path: Path) -> list[Scenario]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    scenarios = []
    for entry in raw.get("scenarios", []):
        expect = entry.get("expect") or {}
        actions = expect.get("actions")
        expected = actions[0] if isinstance(actions, list) and actions else None
        baseline = expect.get("baseline")
        if baseline == "same":
            baseline = expected
        elif not isinstance(baseline, str) or baseline.startswith("n/a"):
            baseline = None
        scenarios.append(
            Scenario(
                id=str(entry["id"]),
                title=str(entry.get("title", "")),
                phase=phase_number(entry.get("phase", 99)),
                fixture=entry.get("fixture"),
                expected=expected,
                baseline=baseline,
            )
        )
    return scenarios


def run(
    scenarios: Sequence[Scenario],
    first_round: Callable[[str, str], str | None],
    *,
    max_phase: float = 5,
    fixtures: set[str] | None = None,
) -> Report:
    """Play each runnable scenario's first round with `baseline` and with `model`.

    Scenarios beyond `max_phase`, or whose fixture the runner does not know, are left out of
    the report entirely - they are not failures, they are not yet runnable.
    """
    report = Report()
    for scenario in scenarios:
        if scenario.phase > max_phase or scenario.fixture is None:
            continue
        if fixtures is not None and scenario.fixture not in fixtures:
            continue
        report.rows.append(
            Row(
                scenario=scenario,
                baseline_action=first_round(scenario.fixture, "baseline"),
                model_action=first_round(scenario.fixture, "model"),
            )
        )
    return report


__all__ = ["Report", "Row", "Scenario", "load_catalogue", "run"]
