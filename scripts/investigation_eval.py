"""Investigation evaluation runs (ADR 0072): the 24 cases, one arm at a time.

    uv run python scripts/investigation_eval.py --decider agent --cap 0.50 --yes-paid
    uv run python scripts/investigation_eval.py --decider enriched --cap 0.50 --yes-paid
    uv run python scripts/investigation_eval.py --decider investigator --cap 1.00 --yes-paid

Arms, on the same cases, the same evidence and the same policies:
- agent         today's workflow (case_investigator_agent v3);
- agent_v5      today's workflow with the post-audit guardrails (v5);
- plain         prompt v4, no tools: one model call on the upfront evidence;
- enriched      code runs the free look-ups, one model call decides (v4);
- investigator  the model chooses its own investigation tools (v4).

Each effect is one pair, everything else equal (audit AUD-11): the prompt is plain vs agent_v5,
the evidence is enriched vs plain, the autonomy is investigator vs enriched. "billed_usd" is read
from the spending guard's call logs: every call the route made, a failed one included.

Every arm makes PAID calls to Claude Haiku through the spending guard: refused without
--yes-paid and a --cap, and the guard refuses the call that would cross the cap. The photo case
(IC-07) has no image check on this route (the Anthropic route reads no images); it is reported,
not hidden. The free, scripted run is tests/evals/investigation (infrastructure only).
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MAX_CAP = Decimal("5")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--decider",
        choices=("agent", "agent_v5", "plain", "enriched", "investigator"),
        required=True,
    )
    parser.add_argument("--cap", type=Decimal, help="USD for the whole run")
    parser.add_argument("--yes-paid", action="store_true", help="confirm real model calls")
    parser.add_argument(
        "--no-langfuse", action="store_true", help="do not import the run (ADR 0074)"
    )
    args = parser.parse_args()
    refused = _refusal(args)
    if refused:
        print(refused)
        return 2

    from dispute_agent.api.investigation_eval import run, save
    from dispute_agent.app import build_haiku_eval_models, eval_toolbox
    from dispute_agent.infra.settings import Settings

    logging.disable(logging.WARNING)
    logs = Path(tempfile.mkdtemp(prefix="inv-eval-"))
    tools = eval_toolbox()

    def models_for(case: Any, turns: list[dict[str, Any]]) -> Any:
        return build_haiku_eval_models(
            Settings(),
            cap_usd=args.cap,
            log_dir=logs,
            engine=args.decider,
            tools=tools,
            record=lambda t: turns.append(dataclasses.asdict(t)),
        )

    record = run(models_for, label=args.decider)
    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

    record["summary"]["billed_usd"] = str(
        sum(
            (open_model_call_log(p).spend_usd("anthropic_direct") for p in logs.glob("calls-*")),
            Decimal(0),
        )
    )
    record["logs"] = str(logs)  # the call logs: per-call tokens, cost and latency for Langfuse
    path = save(record, ROOT)
    for key, value in record["summary"].items():
        print(f"{key:28} {value}")
    print(f"Saved {path}")
    if not args.no_langfuse:  # every paid run is captured (ADR 0074); a no-op without keys
        sys.path.insert(0, str(ROOT / "scripts"))
        from langfuse_eval_import import import_record

        print(import_record(path))
    return 0


def _refusal(args: argparse.Namespace) -> str:
    if not args.yes_paid:
        return "Refused: this makes paid model calls; add --yes-paid to confirm."
    if args.cap is None or not Decimal(0) < args.cap <= MAX_CAP:
        return f"Refused: --cap must be above 0 and at most USD {MAX_CAP}."
    from dispute_agent.infra.settings import Settings

    if not Settings().anthropic_api_key:
        return "Refused: ANTHROPIC_API_KEY is not set in .env."
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
