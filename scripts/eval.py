"""`make eval`: the whole scenario suite, its gates and its report (P6-01/02/03/07).

`make eval` (no options): every non-Phase-7 scenario on recorded model answers - deterministic
and free - then the gates config/release_gates.yaml marks `recorded`, then a markdown report in
data/eval/suite-report.md. Exits 1 when any of those gates fails or is unmeasured.

`make eval ARGS="--live"`: the earlier baseline-versus-model table (P4-11, P4b-11).

Without `--live` the model column is the scripted offline answer, so the command shows the
harness working and the baseline against the catalogue at no cost. With `--live` the model is
your Foundry agent: each scenario's first round costs about half a cent, through the same
spending guard and call log as everything else.
"""

from __future__ import annotations

import argparse
import sys
from functools import cache

from dispute_agent.api.agent_trial import run_trial
from dispute_agent.app import (
    DEFAULT_CONFIG_DIR,
    MODEL_CALL_LOG,
    PROJECT_ROOT,
    build_llm_reasoner,
    build_model_gateway,
    build_model_policy,
)
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.modules.evals.harness import load_catalogue, run
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FIXTURES
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="make eval")
    parser.add_argument("--live", action="store_true", help="the model column is Foundry (costs)")
    parser.add_argument("--max-phase", type=float, default=5)
    args = parser.parse_args()
    if not args.live:
        return suite()

    settings = Settings()
    clock, ids = SystemClock(), UlidIdGenerator()
    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    try:
        if args.live:
            gateway = build_model_gateway(
                "foundry_agent_service", settings=settings, policy=policy, clock=clock, ids=ids
            )
            model = build_llm_reasoner(
                gateway, settings=settings, policy=policy, clock=clock, ids=ids
            )
        else:
            from try_agent import offline_answer

            gateway = build_model_gateway(
                "fake",
                settings=settings,
                policy=policy,
                clock=clock,
                ids=ids,
                fake_answer=offline_answer,
            )
            model = build_llm_reasoner(
                gateway, settings=settings, policy=policy, clock=clock, ids=ids
            )
    except KernelError as failed:
        print(f"eval stopped: {failed}", file=sys.stderr)
        return 2
    baseline = RulesOnlyReasoner(pack=WeightDisputePack(), ids=ids, clock=clock)

    @cache
    def first_round(fixture: str, which: str) -> str | None:
        reasoner = baseline if which == "baseline" else model
        report = run_trial(fixture, reasoner, policy=policy, clock=clock, ids=ids)
        return report.proposal.action.value if report.proposal else None

    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

    log = open_model_call_log(MODEL_CALL_LOG)
    route = "foundry_agent_service" if args.live else "fake"
    before = log.spend_usd(route)
    scenarios = load_catalogue(PROJECT_ROOT / "docs" / "spec" / "eval-scenarios.yaml")
    report = run(scenarios, first_round, max_phase=args.max_phase, fixtures=set(FIXTURES))
    print(
        f"First-round decisions, model = {'Foundry agent (live)' if args.live else 'offline stand-in'}\n"
    )
    print(report.table())
    print(f"\nModel cost of this run: ${log.spend_usd(route) - before:.4f}")
    return 0


def suite() -> int:
    """The recorded suite, its gates and its report. 0 when every recorded gate holds."""
    import logging

    from dispute_agent.api.scenario_runner import play
    from dispute_agent.app import build_scenario_models
    from dispute_agent.modules.evals.scenarios import gates as gate_module
    from dispute_agent.modules.evals.scenarios.catalogue import load, recorded_actions
    from dispute_agent.modules.evals.scenarios.metrics import compute
    from dispute_agent.modules.evals.scenarios.report import render

    logging.disable(logging.WARNING)  # a scenario that escalates logs; the report says it all
    scenarios = load(PROJECT_ROOT / "docs" / "spec" / "eval-scenarios.yaml")
    by_id = {s.id: s for s in scenarios}
    runnable = [s for s in scenarios if s.runnable]
    results = []
    for number, scenario in enumerate(runnable, 1):
        result = play(scenario, recorded_actions(scenario, by_id), build_scenario_models)
        results.append(result)
        print(
            f"  [{number:2}/{len(runnable)}] {scenario.id:5} {'pass' if result.passed else 'FAIL'}"
        )
    measures = compute(runnable, results)
    config = gate_module.load_config(PROJECT_ROOT / "config" / "release_gates.yaml")
    judged = gate_module.evaluate(config, measures, "recorded")
    text = render(
        mode="recorded",
        scenarios=runnable,
        results=results,
        measures=measures,
        gates=judged,
        config_status=str(config.get("status", "")),
    )
    out = PROJECT_ROOT / "data" / "eval" / "suite-report.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed} of {len(results)} scenarios passed (1 run each, recorded answers)")
    for gate in judged:
        if not gate.passed:
            print(f"  GATE FAILED  {gate.name}: {gate.why_failed}")
    held = gate_module.passed(judged)
    print(f"gates: {'all passed' if held else 'FAILED'} ({len(judged)} enforced by this run)")
    print(f"report: {out}")
    return 0 if held else 1


if __name__ == "__main__":
    raise SystemExit(main())
