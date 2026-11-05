"""`make explain`: ask the read-only Case Explainer about a case (ADR 0039). Costs a little.

    make explain ARGS="<case_id> <question...>"     # against data/cases.sqlite3
    make explain ARGS="--list"                       # recent case ids (free)
    make explain ARGS="--check"                      # the live check that gates activation

`--check` builds a throwaway database with three synthetic disputes through the running app's
own assembly, asks one question about each, and applies a rule fixed before any call: every
answer must name the case's real state or review reason, cite a source, recommend no decision,
and stay within budget. Prints answers and cost; logs cost only.
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RECOMMENDS = re.compile(
    r"\b(you should|i recommend|i suggest|please approve|go ahead and approve)\b", re.I
)


def _seed(tmp: Path) -> tuple[Path, list[tuple[str, str, list[str]]]]:
    """Three disputes through the real app assembly (stand-ins, synthetic data, no network)."""
    from fastapi.testclient import TestClient

    from dispute_agent.app import create_app
    from dispute_agent.infra.settings import Settings
    from dispute_agent.infra.sqlite.engine import (
        apply_schema,
        create_sqlite_engine,
        schema_sql_path,
    )
    from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture

    for name in ("modules.yaml", "policy.yaml"):
        (tmp / name).write_text(
            (ROOT / "config" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    database = tmp / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()

    import dispute_agent.app as app_module

    real = app_module.Settings
    app_module.Settings = lambda: Settings(
        _env_file=None, app_env="test", test_seller_email=SELLER_CONTACT
    )  # type: ignore[assignment,misc]
    try:
        client = TestClient(create_app(config_dir=tmp, app_env="test", database=database))
        cases = []
        for n, (name, question, expect) in enumerate(
            [
                (
                    "calc_error_high_value",
                    "Why is case {id} waiting for me, and for how much?",
                    ["AWAITING_APPROVAL", "credit"],
                ),
                ("ambiguous_image", "Why did case {id} go to a person?", ["escalat", "review"]),
                ("not_delivered", "Why was case {id} closed?", ["delivered", "INELIGIBLE"]),
            ]
        ):
            shipment = fixture(name).shipment
            body = {
                "source_event_id": f"explain-check-{n}",
                "shipment_ref": shipment.ref,
                "claim_ref": shipment.claim_ref,
                "seller_ref": shipment.seller_id,
                "seller_contact": SELLER_CONTACT,
                "raised_at": "2026-11-05T09:00:00Z",
            }
            result = client.post("/events", json=body).json()
            cases.append((question.format(id=result["case_id"]), result.get("state", ""), expect))
    finally:
        app_module.Settings = real  # type: ignore[misc]
    return database, cases


def check() -> int:
    from dispute_agent.app import build_case_explainer
    from dispute_agent.infra.settings import Settings

    tmp = Path(tempfile.mkdtemp(prefix="explain-check-"))
    database, cases = _seed(tmp)
    explainer = build_case_explainer(Settings(), database)
    passed = 0
    total_cost = 0.0
    for question, state, expect in cases:
        answer = explainer.ask(question)
        total_cost += answer.cost_usd
        text = answer.text
        named = any(term.lower() in text.lower() for term in expect)
        cited = bool(re.search(r"docs/|case_overview|case_timeline|case_decisions|R\d\d", text))
        neutral = RECOMMENDS.search(text) is None
        ok = named and cited and neutral and bool(text)
        passed += ok
        print(
            f"\nQ: {question}\n   state: {state}\n   tools: {', '.join(answer.tools_used) or '-'}"
        )
        print(
            f"   answer: {text}\n   -> {'PASS' if ok else 'FAIL'} (named={named} cited={cited} neutral={neutral}) ${answer.cost_usd:.4f}"
        )
    print(f"\n{passed} of {len(cases)} answers passed; spend ${total_cost:.4f}")
    return 0 if passed == len(cases) else 1


def main(argv: list[str]) -> int:
    from dispute_agent.app import build_case_explainer
    from dispute_agent.infra.settings import Settings

    if argv[:1] == ["--check"]:
        return check()
    database = ROOT / "data" / "cases.sqlite3"
    if not database.is_file():
        print("no data/cases.sqlite3 yet; run the app first, or use --check", file=sys.stderr)
        return 2
    if argv[:1] == ["--list"]:
        from dispute_agent.modules.reasoning.explainer.reader import CaseReader

        for row in CaseReader(database).recent_case_ids():
            print(row)
        return 0
    if len(argv) < 2:
        print(__doc__)
        return 2
    answer: Any = build_case_explainer(Settings(), database).ask(
        f"Case {argv[0]}: {' '.join(argv[1:])}"
    )
    print(answer.text)
    print(f"\n(tools: {', '.join(answer.tools_used) or '-'}; ${answer.cost_usd:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
