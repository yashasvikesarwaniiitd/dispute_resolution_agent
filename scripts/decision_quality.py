"""Decision Quality on the golden set (ADR 0064).

    uv run python scripts/decision_quality.py                                # rules, free
    uv run python scripts/decision_quality.py --decider haiku --cap 0.10 --yes-paid   # PAID
    uv run python scripts/decision_quality.py --judge-calibration --cap 0.05 --yes-paid  # PAID

Replays every decision point in evals/decision_quality/golden_v1.yaml on the scenario runner's
fakes (no Gmail, no Jira, a simulated ledger), grades each proposal against the owner's answer
key, saves the run to evals/decision_quality/runs/<run_id>.json (tracked; index.json
summarises every run for a dashboard) and prints a summary.

`--decider haiku` spends real money: Claude Haiku on the active investigator prompt decides the
graded round of each point (the rules decide earlier rounds, so both deciders reach the same
evidence). It needs ANTHROPIC_API_KEY in .env, `--cap`, and `--yes-paid`; the spending guard
refuses any call past the cap, and the run stops before a point when less than USD 0.02 is
left. No retries.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from dispute_agent.api.decision_quality import calibrate, meter, rules_only, run, save
from dispute_agent.app import build_scenario_models

MARK = {"acceptable": "OK ", "safe_fallback": "~  ", "not_acceptable": "x  ", "prohibited": "!! "}
MAX_CAP = Decimal("0.50")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", choices=("rules", "haiku"), default="rules")
    parser.add_argument("--cap", type=Decimal, help="USD; required for --decider haiku")
    parser.add_argument("--yes-paid", action="store_true", help="confirm real model calls")
    parser.add_argument(
        "--judge-calibration", action="store_true", help="run the LLM judge on its calibration set"
    )
    parser.add_argument(
        "--judge-route", choices=("anthropic_direct", "foundry_openai"), default="anthropic_direct"
    )
    args = parser.parse_args()

    if args.judge_calibration:
        args.decider = "judge"
        refused = _refusal(args)
        if refused:
            print(refused)
            return 2
        return _calibrate(args)
    if args.decider == "rules":
        record = run(models=rules_only(build_scenario_models))
    else:
        refused = _refusal(args)
        if refused:
            print(refused)
            return 2
        from dispute_agent.app import build_haiku_eval_models
        from dispute_agent.infra.settings import Settings

        logging.disable(logging.WARNING)
        logs = Path(tempfile.mkdtemp(prefix="dq-haiku-"))  # one call log per point
        models = build_haiku_eval_models(Settings(), cap_usd=args.cap, log_dir=logs)
        record = run(models=models, label="haiku", usage=meter(logs), budget_usd=args.cap)
    path = save(record)
    _print(record)
    print(f"Saved {path}")
    return 0


def _calibrate(args: argparse.Namespace) -> int:
    from dispute_agent.app import build_decision_judge
    from dispute_agent.infra.settings import Settings

    logging.disable(logging.WARNING)
    log = Path(tempfile.mkdtemp(prefix="dq-judge-")) / "calls.sqlite3"
    judge = build_decision_judge(Settings(), route=args.judge_route, cap_usd=args.cap, log_path=log)
    record = calibrate(judge, usage=meter(log), budget_usd=args.cap)
    path = save(record)
    print(f"LLM judge calibration - run {record['run_id']} ({record['calibration_status']} labels)")
    print(f"model {record['model']}; prompt {record['prompt_version']}")
    print()
    for item in record["items"]:
        if "error" in item or "not_run" in item:
            print(f"  ?   {item['id']}  {item.get('error') or item.get('not_run')}")
            continue
        mark = "OK " if item["agrees"] else "x  "
        crit = " [critical]" if item["critical"] else ""
        claims = "; ".join(c["claim"] for c in item["unsupported_claims"])
        print(
            f"  {mark} {item['id']}  owner {item['owner']}, judge {item['judge']}{crit}  {claims}"
        )
    agree = record["agreement"]
    print()
    print(
        f"Agree {agree['agree']} of {agree['n']}; misses {agree['misses']}; "
        f"false alarms {agree['false_alarms']}; critical caught {agree['critical_caught']} "
        f"of {agree['critical']}; errors {record['errors']}; not run {record['not_run']}"
    )
    print(f"Cost USD {record['cost_usd']} (cap {record['budget_usd']}). Saved {path}")
    return 0


def _refusal(args: argparse.Namespace) -> str:
    """Why a paid run may not start, or "" when it may."""
    if not args.yes_paid:
        return "Refused: this makes paid model calls; add --yes-paid to confirm."
    if args.cap is None or not Decimal(0) < args.cap <= MAX_CAP:
        return f"Refused: --cap must be above 0 and at most USD {MAX_CAP}."
    from dispute_agent.infra.settings import Settings

    if not Settings().anthropic_api_key:
        return "Refused: ANTHROPIC_API_KEY is not set in .env."
    return ""


def _print(record: dict[str, object]) -> None:
    summary = record["summary"]["overall"]  # type: ignore[index]
    print(f"Decision Quality - {record['decider']} - run {record['run_id']}")
    print(
        f"golden v{record['golden_version']} ({str(record['golden_digest'])[:12]}), "
        f"grader v{record['grader_version']}, commit {record['git_commit']}"
    )
    if record["model"]:
        print(f"model {record['model']}; prompt {record['prompt_version']}")
    print()
    for point in record["points"]:  # type: ignore[attr-defined]
        if "not_reached" in point or "not_run" in point:
            print(f"  ?   {point['id']}  {point.get('not_reached') or point.get('not_run')}")
            continue
        credit = (
            f" ({point['reason_code']}, INR {point['amount_inr']})" if point["amount_inr"] else ""
        )
        fell = " [fell back to rules]" if point.get("fell_back") else ""
        why = f"  - {'; '.join(point['problems'])}" if point["problems"] else ""
        print(f"  {MARK[point['grade']]} {point['id']}  {point['action']}{credit}{fell}{why}")
    print()
    print(
        f"Acceptable {summary['acceptable']} of {summary['n']}; "
        f"safe fallback {summary['safe_fallback']}; not acceptable {summary['not_acceptable']}; "
        f"prohibited {summary['prohibited']}; not reached {len(record['not_reached'])}; "  # type: ignore[arg-type]
        f"not run {len(record['not_run'])}"  # type: ignore[arg-type]
    )
    if record["model_calls"]:
        print(
            f"Model calls {record['model_calls']}; cost USD {record['cost_usd']} "
            f"(cap {record['budget_usd']}); tokens {record['tokens']}"
        )


if __name__ == "__main__":
    sys.exit(main())
