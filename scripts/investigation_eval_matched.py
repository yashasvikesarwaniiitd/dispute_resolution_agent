"""Matched investigation evaluation (ADR 0072-0074): the same cases on several architectures,
arm-interleaved, one pass, one combined budget. The runner behind paid runs 1-3.

    uv run python scripts/investigation_eval_matched.py --dry                 (free: no network)
    uv run python scripts/investigation_eval_matched.py --paid --cap 0.10     (real Claude Haiku)

Each paid run is saved to evals/investigation/runs/ and imported into Langfuse automatically
when LANGFUSE_* keys are set (ADR 0074; --no-langfuse to skip), with every prompt and reply
recorded (synthetic evaluation data only; production tracing still sends no content).

Budget, in code:
- every arm and case writes to ONE folder of call logs; each replay's guard gets as its cap
  what is left of GUARD_CAP across all of them, and refuses any call once that is spent;
- the guard checks before a call, so the last call could overshoot by one call: GUARD_CAP is
  the total minus the worst case of one guarded call (two attempts, the largest prompt,
  the full output limit, at cache-write prices);
- cases run arm-interleaved, so if the money runs out every arm has the same cases;
- retries and failed calls are in the logs (usage summed over attempts; a timeout is charged
  at worst case), so they count.
Synthetic replays only: the scenario runner's world has fake mail, fake Jira, a simulated ledger.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sqlite3
import sys
import tempfile
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
#: Runs 1-3's selection: the defaults keep later runs comparable.
CASES = ["IC-01", "IC-03", "IC-04", "IC-06", "IC-09", "IC-12", "IC-13", "IC-15", "IC-16", "IC-20"]
ARMS = ["agent", "enriched", "investigator"]
MAX_CAP = Decimal("5")
#: What the investigator's turn log keeps of a tool result (MAX_LOGGED_RESULT_CHARS).
TOOL_RESULT_CHARS = 1500
# Worst case of one guarded call: 2 attempts x (40k prompt tokens at 1.25x cache-write price
# + 4000 output tokens). Haiku 5.5: USD 0.10 in / 0.50 out per million tokens.
MAX_PROMPT_TOKENS = 40_000
MAX_OUT = 4000
RESERVE = (
    2
    * (
        Decimal(MAX_PROMPT_TOKENS) * Decimal("0.10") * Decimal("1.25")
        + Decimal(MAX_OUT) * Decimal("0.50")
    )
    / Decimal(1_000_000)
)


def spent(logs: Path) -> Decimal:
    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

    return sum(
        (
            open_model_call_log(p).spend_usd("anthropic_direct")
            for p in logs.glob("calls-*.sqlite3")
        ),
        Decimal(0),
    )


def calls_in(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in sorted(paths):
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row
        rows += [dict(r) for r in con.execute("select * from model_calls order by created_at")]
        con.close()
    return rows


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry", action="store_true")
    mode.add_argument("--paid", action="store_true")
    parser.add_argument(
        "--cap", type=Decimal, default=Decimal("0.10"), help="USD, all arms together"
    )
    parser.add_argument("--cases", default=",".join(CASES))
    parser.add_argument("--arms", default=",".join(ARMS))
    parser.add_argument("--no-langfuse", action="store_true")
    args = parser.parse_args()
    total_cap = Decimal(args.cap)
    guard_cap = total_cap - RESERVE
    if not Decimal(0) < total_cap <= MAX_CAP or guard_cap <= 0:
        print(f"Refused: --cap must be above {RESERVE} and at most {MAX_CAP}.")
        return 2
    case_ids, arms = args.cases.split(","), args.arms.split(",")

    from dispute_agent.api.decision_quality import at_round
    from dispute_agent.api.investigation_eval import replay
    from dispute_agent.app import build_haiku_eval_models, eval_toolbox
    from dispute_agent.infra.settings import Settings
    from dispute_agent.modules.evals.investigation import grade, load_cases

    settings = Settings()
    if args.paid and not settings.anthropic_api_key:
        print("Refused: ANTHROPIC_API_KEY is not set.")
        return 2
    current_arm = {"arm": ""}
    if args.dry:
        _fake_provider(current_arm)

    logging.disable(logging.WARNING)
    logs = Path(tempfile.mkdtemp(prefix="inv-focused-"))
    tools = eval_toolbox()  # no image checker: no vision calls
    cases = {c.id: c for c in load_cases().cases}
    results: list[dict[str, Any]] = []
    stopped = ""
    for case_id in case_ids:
        case = cases[case_id]
        assert not case.requires_vision and case.round == 1
        left = guard_cap - spent(logs)
        if left <= 0:
            stopped = f"budget spent before {case_id}"
            break
        for arm in arms:
            current_arm["arm"] = arm
            turns: list[dict[str, Any]] = []
            held: dict[str, Any] = {}
            before = set(logs.glob("calls-*.sqlite3"))

            def models_for(
                _case: Any,
                _turns: list[dict[str, Any]],
                arm: str = arm,
                turns: list[Any] = turns,
                held: dict[str, Any] = held,
            ) -> Any:
                def keep(turn: Any) -> None:
                    turns.append(dataclasses.asdict(turn))

                factory = build_haiku_eval_models(
                    settings,
                    cap_usd=guard_cap,
                    log_dir=logs,
                    engine=arm,
                    tools=tools,
                    record=keep,
                )

                def wrapped(*a: Any, held: dict[str, Any] = held) -> Any:
                    held["models"] = factory(*a)
                    return held["models"]

                return wrapped

            started = time.monotonic()
            # Every Anthropic gateway this case builds, to read how each call's format was sent.
            import dispute_agent.modules.model_gateway.anthropic_direct.gateway as adg

            made: list[Any] = []
            original_post_init = adg.AnthropicDirectGateway.__post_init__

            content: list[dict[str, Any]] = []

            def tracking(
                self: Any,
                _orig: Any = original_post_init,
                made: list[Any] = made,
                content: list[dict[str, Any]] = content,
            ) -> None:
                _orig(self)
                made.append(self)
                if not args.dry:
                    self.client = _Recording(self.client, content)  # every prompt and reply

            adg.AnthropicDirectGateway.__post_init__ = tracking  # type: ignore[method-assign]
            outcome = replay(case, at_round(models_for(case, turns), case.round))
            wall_ms = int((time.monotonic() - started) * 1000)
            adg.AnthropicDirectGateway.__post_init__ = original_post_init  # type: ignore[method-assign]
            sent = [dict(e) for gw in made for e in gw.sent_log]
            if len(content) == len(sent):
                sent = [s | c for s, c in zip(sent, content, strict=True)]
            graded = grade(
                case,
                outcome.action,
                [t for t in turns if t.get("gather_round") == case.round],
                rationale=outcome.rationale,
                findings=list(outcome.findings),
                flags=list(outcome.flags),
                vision=False,
            )
            row = graded.model_dump() | {
                "reason_code": outcome.reason_code,
                "not_reached": outcome.not_reached,
                "cited": sorted(
                    {r for f in outcome.findings for r in f.get("evidence_refs") or ()}
                ),
                "flags": list(outcome.flags),
                "rationale": outcome.rationale,
                "findings": [
                    {
                        "kind": f.get("kind"),
                        "statement": f.get("statement"),
                        "refs": f.get("evidence_refs"),
                    }
                    for f in outcome.findings
                ],
            }
            new = sorted(set(logs.glob("calls-*.sqlite3")) - before)
            calls = calls_in(new)
            inner = getattr(getattr(held.get("models"), "reasoner", None), "inner", None)
            inner = getattr(inner, "inner", inner)  # AtRound -> TwoPass -> engine
            if arm == "agent":
                steps = list(getattr(inner, "trace", []) or [])
                trace = [{"step": s} for s in steps]
                tool_calls = sum(1 for s in steps if s != "decide")
            else:
                trace = [dict(t) for t in turns]  # every turn field, the tool result as logged
                tool_calls = row["tool_calls"]
            results.append(
                {
                    "case": case_id,
                    "category": case.category,
                    "arm": arm,
                    "acceptable_actions": case.acceptable,
                    "prohibited_actions": case.prohibited,
                    **{
                        k: row.get(k)
                        for k in (
                            "action",
                            "grade",
                            "consulted",
                            "grounded",
                            "understood",
                            "wrongly_flagged",
                            "within_budget",
                            "over_investigated",
                            "unnecessary_hub_request",
                            "unnecessary_seller_request",
                            "reason_code",
                            "cited",
                            "flags",
                            "not_reached",
                        )
                    },
                    "tool_calls": tool_calls,
                    "model_calls": len(calls),
                    "failed_calls": sum(1 for c in calls if c.get("status") != "ok"),
                    "call_errors": [c.get("error_code") for c in calls if c.get("error_code")],
                    "input_tokens": sum(int(c.get("input_tokens") or 0) for c in calls),
                    "output_tokens": sum(int(c.get("output_tokens") or 0) for c in calls),
                    "cost_usd": str(
                        sum(
                            (Decimal(int(c.get("cost_usd_micros") or 0)) for c in calls), Decimal(0)
                        )
                        / Decimal(1_000_000)
                    ),
                    "max_call_cost_usd": str(
                        max(
                            (Decimal(int(c.get("cost_usd_micros") or 0)) for c in calls),
                            default=Decimal(0),
                        )
                        / Decimal(1_000_000)
                    ),
                    "model_latency_ms": sum(int(c.get("latency_ms") or 0) for c in calls),
                    "wall_ms": wall_ms,
                    "models": sorted({str(c.get("model_id")) for c in calls}),
                    "prompts": sorted(
                        {f"{c.get('prompt_name')}/{c.get('prompt_version')}" for c in calls}
                    ),
                    "trace": trace,
                    "provider_calls": sent,
                    "rationale": row.get("rationale", ""),
                    "findings": row.get("findings", []),
                }
            )
            print(
                f"{case_id} {arm:12} {row.get('action')!s:24} {row.get('grade'):15} "
                f"calls={len(calls)} tools={tool_calls} total_spent={spent(logs):.6f}",
                flush=True,
            )
    total = spent(logs)
    out = {
        "mode": "dry" if args.dry else "paid",
        "ran_at": datetime.now(UTC).isoformat(),
        "cases": case_ids,
        "arms": arms,
        "tool_result_chars": TOOL_RESULT_CHARS,
        "total_cap_usd": str(total_cap),
        "guard_cap_usd": str(guard_cap),
        "reserve_usd": str(RESERVE),
        "billed_usd": str(total),
        "stopped": stopped,
        "logs": str(logs),
        "results": results,
    }
    folder = ROOT / "evals" / "investigation" / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"inv-focused-{out['mode']}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.json"
    path.write_text(json.dumps(out, indent=1, default=str) + "\n", encoding="utf-8")
    print(f"billed_usd={total} (cap {total_cap}); saved {path}")
    if args.paid and not args.no_langfuse:
        sys.path.insert(0, str(ROOT / "scripts"))
        from langfuse_eval_import import import_record

        print(json.dumps(import_record(path), indent=1, default=str))
    return 0


class _Recording:
    """The Anthropic client, keeping each request's text and each reply's text (evaluation
    runs only, on synthetic data: ADR 0074). Everything else passes straight through."""

    def __init__(self, client: Any, into: list[dict[str, Any]]) -> None:
        self._client, self.messages = client, _Messages(client.messages, into)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


class _Messages:
    def __init__(self, messages: Any, into: list[dict[str, Any]]) -> None:
        self._messages, self._into = messages, into

    def create(self, **kwargs: Any) -> Any:
        sent = {"system": _plain(kwargs.get("system")), "user": _plain(kwargs.get("messages"))}
        try:
            response = self._messages.create(**kwargs)
        except Exception as failed:
            self._into.append({"input": sent, "output": f"no reply: {type(failed).__name__}"})
            raise
        reply = "".join(getattr(b, "text", "") for b in getattr(response, "content", []))
        self._into.append({"input": sent, "output": reply})
        return response


def _plain(value: Any) -> str:
    """A system prompt or message list as text, whatever blocks the adapter used."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_plain(v) for v in value)
    if isinstance(value, dict):
        return _plain(value.get("text") or value.get("content") or "")
    return ""


def _fake_provider(current_arm: dict[str, str]) -> None:
    """--dry: the provider call answers 'escalate' without any network, with notional usage."""
    from dispute_agent.modules.evals.scenarios.answers import investigator
    from dispute_agent.modules.model_gateway.anthropic_direct import gateway as g
    from dispute_agent.modules.model_gateway.output import RawReply

    def fake_call(
        self: Any, system: str, user: str, max_tokens: int, prompt_chars: int, schema: Any = None
    ) -> RawReply:
        native, _ = self._native(schema)
        self.sent_log.append(
            {
                "mode": "native" if native else "plain",
                "schema": str((schema or {}).get("title", "")),
                "latency_ms": 1,
            }
        )
        draft = investigator("escalate_to_human", None)
        if current_arm["arm"] == "agent":
            body = {"step": "decide", "argument": "", "draft": draft}
        else:
            body = {"step": "decide", "arguments": {}, "draft": draft}
        tin, tout = prompt_chars // 4, 300
        return RawReply(
            text=json.dumps(body),
            input_tokens=tin,
            output_tokens=tout,
            latency_ms=1,
            cost_usd=g.cached_cost(self.price, tin, 0, 0, tout),
            model_id="dry-run",
        )

    g.AnthropicDirectGateway._call = fake_call  # type: ignore[method-assign]


if __name__ == "__main__":
    raise SystemExit(main())
