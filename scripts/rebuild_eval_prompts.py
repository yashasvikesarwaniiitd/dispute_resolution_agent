"""Rebuild what a past evaluation run showed the agent first (ADR 0074). No model is called.

    uv run python scripts/rebuild_eval_prompts.py evals/investigation/runs/<record>.json

Runs 1-3 did not save their prompts. Each case replays synthetic data in a fake world, so the
first prompt of each execution (case x architecture) can be rebuilt exactly: the same replay
runs with a stand-in for the model that keeps the first prompt and answers "escalate". Later
prompts depended on the model's own replies, which were not saved, so they are not rebuilt.

Check: the instructions' fingerprint must equal the one the run's call log recorded for that
first call (the case text itself is not fingerprinted; it comes from the same unchanged code
and data). Writes <record>.inputs.json beside the record; the record itself is not changed.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
#: One look-up in a prompt's investigation notes: "- T1.2: tool({args}) -> {answer}".
NOTE = re.compile(r"^- (T\d+\.\d+): (\w+)\((.*?)\) -> (.*)$", re.M)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    path = Path(sys.argv[1])
    record = json.loads(path.read_text(encoding="utf-8"))

    from langfuse_eval_import import call_logs

    from dispute_agent.api.decision_quality import at_round
    from dispute_agent.api.investigation_eval import replay
    from dispute_agent.app import build_haiku_eval_models, eval_toolbox
    from dispute_agent.infra.settings import Settings
    from dispute_agent.kernel.domain.hashing import payload_hash
    from dispute_agent.modules.evals.investigation import load_cases
    from dispute_agent.modules.evals.scenarios.answers import investigator
    from dispute_agent.modules.model_gateway.anthropic_direct import gateway as g
    from dispute_agent.modules.model_gateway.output import RawReply

    seen: list[tuple[str, str]] = []
    arm_now = {"arm": ""}
    replaying: list[dict[str, Any]] = []  # the run's own look-ups, asked again in order

    def stand_in(
        self: Any, system: str, user: str, max_tokens: int, prompt_chars: int, schema: Any = None
    ) -> RawReply:
        seen.append((system, user))
        if replaying:
            step = replaying.pop(0)
            body: dict[str, Any] = {
                "step": step["step"],
                "arguments": json.loads(step["arguments"] or "{}"),
                "reason": "replay of the recorded run",
            }
        else:
            draft = investigator("escalate_to_human", None)
            key = "argument" if arm_now["arm"] == "agent" else "arguments"
            body = {"step": "decide", key: "" if key == "argument" else {}, "draft": draft}
        return RawReply(
            text=json.dumps(body),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_usd=Decimal(0),
            model_id="rebuild",
        )

    g.AnthropicDirectGateway._call = stand_in  # type: ignore[method-assign]
    logging.disable(logging.WARNING)
    settings = Settings()
    tools = eval_toolbox()
    cases = {c.id: c for c in load_cases().cases}
    logged = call_logs(record)
    out: dict[str, Any] = {}
    for n, r in enumerate(record["results"]):
        arm_now["arm"], seen[:] = r["arm"], []
        steps = [t for t in r["trace"] if t.get("ref") and t.get("arguments") is not None]
        # The enriched arm's look-ups run in code; only the investigator chose its own.
        replaying[:] = [t for t in steps if r["arm"] == "investigator"]
        case = cases[r["case"]]
        factory = build_haiku_eval_models(
            settings,
            cap_usd=Decimal(1),
            log_dir=Path(tempfile.mkdtemp(prefix="rebuild-")),
            engine=r["arm"],
            tools=tools,
            record=lambda t: None,
        )
        replay(case, at_round(factory, case.round))
        system, user = seen[0]
        recorded = (logged[n][0].get("prompt_hash") if logged and logged[n] else None) or ""
        matches = recorded == str(payload_hash(system))
        answers: dict[str, str] = {}  # each look-up's full answer, as the next prompt showed it
        for _, later in seen:
            for ref, _tool, _args, answer in NOTE.findall(later):
                answers[ref] = answer
        # Check: each answer starts with exactly what the run kept of it (its first 400 chars).
        kept = {t["ref"]: str(t.get("result") or "") for t in steps}
        tools_ok = all(answers.get(ref, "").startswith(v) for ref, v in kept.items() if v)
        out[f"{r['case']}/{r['arm']}"] = {
            "system": system,
            "user": user,
            "verified": matches,
            "tools": {ref: answers[ref] for ref in kept if ref in answers} if tools_ok else {},
        }
        print(
            f"{r['case']} {r['arm']:12} instructions match the run: {matches}; "
            f"{len(kept)} look-up answers recovered and matching: {tools_ok}",
            flush=True,
        )
        matches = matches and tools_ok
    target = path.with_suffix(".inputs.json")
    target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    bad = [k for k, v in out.items() if not v["verified"]]
    print(f"saved {target}; not matching: {bad or 'none'}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
