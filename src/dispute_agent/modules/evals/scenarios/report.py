"""The eval report: gates, metrics and every scenario, in markdown (P6-07).

Every figure carries its sample - "56 of 57 scenarios, 1 run each" - because the gates file
forbids quoting a rate without one. The opening paragraph says what the numbers do and do not
show, so the report cannot be lifted out of context as a claim it does not support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.modules.evals.scenarios.catalogue import Scenario
    from dispute_agent.modules.evals.scenarios.checks import Result
    from dispute_agent.modules.evals.scenarios.gates import GateResult
    from dispute_agent.modules.evals.scenarios.metrics import Measure

PERCENT = {
    "injection_success_rate",
    "recorded_fixture_suite_pass_rate",
    "correct_action_rate",
    "correct_final_outcome_rate",
    "escalation_rate_when_not_expected",
    "missed_escalation_rate",
    "invalid_output_rate",
    "evidence_completeness",
}

LIMITS = (
    "These results come from a synthetic pilot: synthetic shipments, fake email and Jira, a "
    "simulated ledger and a fake clock. The recorded run fixes the model's choices to the ones "
    "each scenario declares, so it measures the system around the model - policy, approvals, the "
    "executor, verification and timers - not the model. They do not show production readiness, "
    "and they say nothing about real sellers, real tariffs or real operator time."
)


def render(
    *,
    mode: str,
    scenarios: Sequence[Scenario],
    results: Sequence[Result],
    measures: dict[str, Measure],
    gates: Sequence[GateResult],
    runs: int = 1,
    config_status: str = "",
) -> str:
    by_id = {s.id: s for s in scenarios}
    passed = sum(1 for r in results if r.passed)
    held = all(g.passed for g in gates) and bool(gates)
    lines = [
        f"# Evaluation report ({mode} run)",
        "",
        f"> {LIMITS}",
        "",
        f"- Scenarios run: **{len(results)}** (catalogue minus the two Phase 7 scenarios), "
        f"{runs} run each.",
        f"- Scenarios passing every declared expectation: **{passed} of {len(results)}**.",
        f"- Gates enforced by this run: {len(gates)}; result: "
        f"**{'all passed' if held else 'FAILED'}**."
        + (f" Thresholds are `{config_status}`." if config_status else ""),
        "",
        "## Gates",
        "",
        "| Gate | Group | Threshold | Measured | Result |",
        "|------|-------|-----------|----------|--------|",
    ]
    for gate in gates:
        shown = gate.measure.render(percent=gate.name in PERCENT) if gate.measure else "no metric"
        verdict = "pass" if gate.passed else f"**FAIL** - {gate.why_failed}"
        lines.append(f"| {gate.name} | {gate.group} | {gate.rule} | {shown} | {verdict} |")
    lines += ["", "## All metrics", "", "| Metric | Value |", "|--------|-------|"]
    for name, measure in sorted(measures.items()):
        lines.append(f"| {name} | {measure.render(percent=name in PERCENT)} |")
    lines += [
        "",
        "## Scenarios",
        "",
        "| Id | Title | Expected final | Actual final | Result |",
        "|----|-------|----------------|--------------|--------|",
    ]
    for result in results:
        scenario = by_id.get(result.scenario_id)
        title = (scenario.title if scenario else "").replace("|", "/")
        expected = scenario.expect.get("final") if scenario else None
        verdict = "pass" if result.passed else "FAIL: " + "; ".join(result.mismatches)[:300]
        lines.append(
            f"| {result.scenario_id} | {title} | {expected or '-'} | "
            f"{result.outcome.final or '-'} | {verdict.replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


__all__ = ["LIMITS", "render"]
