"""Import an investigation evaluation run record into Langfuse (ADR 0074).

    uv run python scripts/langfuse_eval_import.py evals/investigation/runs/<record>.json --dry-run
    uv run python scripts/langfuse_eval_import.py evals/investigation/runs/<record>.json

Synthetic evaluation data only. No model is called. Safe to run again: a trace already sent
with the same content is skipped (evals/investigation/runs/langfuse_manifest.json), scores and
dataset items are upserted by id, and queue items and score configs are reused.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "evals" / "investigation" / "runs" / "langfuse_manifest.json"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser()
    parser.add_argument("record", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="build and summarise; send nothing")
    parser.add_argument(
        "--replace",
        default="",
        help="comma-separated trace ids to DELETE in Langfuse and send once again under new ids "
        "(irreversible; only with the owner's approval)",
    )
    args = parser.parse_args()
    report = import_record(
        args.record, dry_run=args.dry_run, replace=[x for x in args.replace.split(",") if x]
    )
    print(json.dumps(report, indent=1, default=str))
    return 1 if report.get("problems") else 0


def import_record(
    path: Path, *, dry_run: bool = False, replace: list[str] | None = None
) -> dict[str, Any]:
    """Build the record's traces and, unless `dry_run` or Langfuse keys are missing, send them."""
    from dispute_agent.api.eval_traces import LangfuseEvalExport, build, reissued
    from dispute_agent.infra.settings import Settings
    from dispute_agent.modules.evals.investigation import load_cases

    record = json.loads(path.read_text(encoding="utf-8"))
    rebuilt = path.with_suffix(".inputs.json")  # scripts/rebuild_eval_prompts.py
    if rebuilt.exists():
        first = json.loads(rebuilt.read_text(encoding="utf-8"))
        for r in record.get("results") or ():
            found = first.get(f"{r['case']}/{r['arm']}")
            if found and found.get("verified"):  # only a rebuild that matched the run
                r["first_prompt"] = {"system": found["system"], "user": found["user"]}
                r["tool_results"] = found.get("tools") or {}  # each matched what the run kept
    cases = {c.id: c.model_dump() for c in load_cases().cases}
    calls = call_logs(record)
    executions = build(record, cases, calls, run_id=run_id(path), reissued=reissued(MANIFEST))
    summary: dict[str, Any] = {
        "record": path.name,
        "executions": len(executions),
        "observations": sum(1 + len(e.children) for e in executions),
        "scores": sum(len(e.scores) for e in executions),
        "for_review": sum(e.review for e in executions),
    }
    settings = Settings()
    if dry_run:
        return summary | {"sent": False, "why": "dry run"}
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return summary | {"sent": False, "why": "LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set"}
    export = LangfuseEvalExport(
        settings.langfuse_host,
        settings.langfuse_public_key,
        settings.langfuse_secret_key.get_secret_value(),
    )
    deleted: list[str] = []
    if replace:
        chosen = [e for e in executions if e.trace_id in set(replace)]
        if len(chosen) != len(set(replace)):
            return summary | {
                "sent": False,
                "why": "--replace names a trace this record did not send",
            }
        problems = export.delete(chosen, MANIFEST)
        if problems:
            return summary | {"sent": False, "problems": problems}
        deleted = [e.trace_id for e in chosen]
        executions = build(record, cases, calls, run_id=run_id(path), reissued=reissued(MANIFEST))
    report = export.send(executions, MANIFEST, label=run_id(path))
    return (
        summary
        | {"sent": True, "host": settings.langfuse_host, "deleted": deleted}
        | report.__dict__
    )


def run_id(path: Path) -> str:
    return path.stem


def call_logs(record: dict[str, Any]) -> list[list[dict[str, Any]]] | None:
    """Each execution's model-call log rows, in run order, when the run's log folder remains."""
    folder = Path(str(record.get("logs") or ""))
    files = sorted(folder.glob("calls-*.sqlite3")) if record.get("logs") and folder.is_dir() else []
    if not files or len(files) != len(record.get("results") or record.get("cases") or ()):
        return None
    out = []
    for f in files:
        with sqlite3.connect(f) as con:
            con.row_factory = sqlite3.Row
            out.append([dict(r) for r in con.execute("select * from model_calls order by rowid")])
    return out


if __name__ == "__main__":
    raise SystemExit(main())
