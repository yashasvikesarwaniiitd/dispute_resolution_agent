"""The agentic investigator's live check (ADR 0042). Costs a little; run only with approval.

    PROMPTS_ALLOW_INACTIVE=true REASONING_ENGINE=agent uv run python scripts/agent_check.py --cap 0.25

Scored **per distinct dispute**, not per scenario (the lesson of ADR 0041: 57 scenarios rest on
13 disputes). Each dispute is investigated once by the agentic engine and once by the
single-call engine, on the same gpt-5-mini deployment (the direct `foundry_openai` route - no
hosted agent is needed for a new prompt). For each it prints the expected first step, both
engines' choices, and the lookups the agent chose to make. Counts and choices only; no text.

The pass rule, fixed before any call: the agent must finish every dispute with a valid
decision (no `invalid_model_output`), use at least one lookup somewhere in the run, and stay
under the cap. Accuracy is reported, not gated - 13 single runs cannot settle it.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Any

from dispute_agent.api.agent_trial import run_trial
from dispute_agent.app import (
    DEFAULT_CONFIG_DIR,
    MODEL_CALL_LOG,
    build_llm_reasoner,
    build_model_gateway,
    build_model_policy,
)
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
from dispute_agent.modules.evals.harness import load_catalogue
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import FIXTURES

ROOT = Path(__file__).resolve().parents[1]
ROUTE = "foundry_openai"


def disputes() -> OrderedDict[str, str]:
    """Each distinct dispute and the first step its first scenario expects."""
    found: OrderedDict[str, str] = OrderedDict()
    for scenario in load_catalogue(ROOT / "docs" / "spec" / "eval-scenarios.yaml"):
        fixture, first = scenario.fixture, scenario.expected
        if fixture in FIXTURES and fixture not in found and first and first != "n/a":
            found[fixture] = str(first)
    return found


def reasoner_for(engine: str, settings: Settings, policy: Any, clock: Any, ids: Any) -> Any:
    os.environ["REASONING_ENGINE"] = engine
    gateway = build_model_gateway(ROUTE, settings=settings, policy=policy, clock=clock, ids=ids)
    return build_llm_reasoner(
        gateway,
        settings=settings.model_copy(update={"reasoning_engine": engine}),
        policy=policy,
        clock=clock,
        ids=ids,
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    logging.disable(logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--cap", type=Decimal, required=True)
    args = parser.parse_args()
    settings = Settings()
    clock, ids = SystemClock(), UlidIdGenerator()
    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    log = open_model_call_log(MODEL_CALL_LOG)
    start = log.spend_usd(ROUTE)

    agent = reasoner_for("agent", settings, policy, clock, ids)
    single = reasoner_for("single", settings, policy, clock, ids)
    inner = getattr(agent, "inner", agent)  # TwoPassReasoner wraps the engine

    rows, invalid, lookups = [], 0, 0
    print(f"{'dispute':26} {'expected':24} {'single':24} {'agent':24} agent steps")
    for fixture, expected in disputes().items():
        if log.spend_usd(ROUTE) - start > args.cap - Decimal("0.03"):
            print(f"stopped before {fixture}: near the cap")
            break
        got: dict[str, str] = {}
        for name, reasoner in (("single", single), ("agent", agent)):
            report = run_trial(fixture, reasoner, policy=policy, clock=clock, ids=ids)
            review = report.case.review_reason if report.case else None
            got[name] = (
                report.proposal.action.value
                if report.proposal
                else (review.code.value if review else (report.refused or "-"))
            )
        trace = list(getattr(inner, "trace", []))
        lookups += sum(1 for s in trace if s != "decide")
        invalid += got["agent"] == "invalid_model_output"
        rows.append((fixture, expected, got["single"], got["agent"]))
        print(
            f"{fixture:26} {expected:24} {got['single']:24} {got['agent']:24} {' > '.join(trace) or '-'}"
        )

    spent = log.spend_usd(ROUTE) - start
    n = len(rows)
    right = {k: sum(1 for r in rows if r[1] == r[i]) for k, i in (("single", 2), ("agent", 3))}
    print(
        f"\ncorrect first step: single {right['single']} of {n}, agent {right['agent']} of {n} (one run each)"
    )
    print(
        f"agent lookups used: {lookups}; invalid decisions: {invalid}; spend ${spent:.4f} (cap ${args.cap})"
    )
    ok = n > 0 and invalid == 0 and lookups > 0 and spent <= args.cap
    print(
        f"verdict: {'PASS' if ok else 'FAIL'} (rule: every dispute decided validly, lookups used, under cap)"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
