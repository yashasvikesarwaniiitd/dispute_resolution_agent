"""Decision Quality's model path, offline (ADR 0064): no real model is called here.

The scripted model stands in for Haiku. What is checked is the harness the paid run relies on:
the model decides only the graded round (the rules decide earlier ones, so every decider meets
the same evidence), the run's meter records usage, and the budget stops the run before a point
instead of during it.
"""

from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any

from dispute_agent.api.decision_quality import run
from dispute_agent.app import build_scenario_models
from dispute_agent.modules.evals.decision_quality import load_golden

GOLDEN = load_golden()


def _subset(*ids: str) -> Any:
    return GOLDEN.model_copy(
        update={"decision_points": tuple(p for p in GOLDEN.decision_points if p.id in ids)}
    )


class _Meter:
    """A stand-in for the call log: each reading costs a fixed amount more than the last."""

    def __init__(self, step: str) -> None:
        self.step, self.spent, self.calls = Decimal(step), Decimal(0), 0

    def __call__(self) -> dict[str, Any]:
        reading = {
            "calls": self.calls,
            "cost_usd": self.spent,
            "tokens_in": 10 * self.calls,
            "tokens_out": 5 * self.calls,
            "latency_ms": 100 * self.calls,
            "models": ["scripted"],
            "prompts": ["case_investigator v1"],
        }
        self.spent += self.step
        self.calls += 1
        return reading


def test_the_model_decides_only_the_graded_round() -> None:
    # DQ-14 is round 2: the rules ask the hub in round 1, then the scripted model decides.
    record = run(
        _subset("DQ-01", "DQ-14"),
        models=build_scenario_models,
        label="scripted",
        usage=_Meter("0.001"),
    )
    by_id = {p["id"]: p for p in record["points"]}
    assert by_id["DQ-14"]["reasoner"] == "llm" and by_id["DQ-01"]["reasoner"] == "llm"
    assert record["not_reached"] == []  # round 1 went as the rules decide, so round 2 came
    assert by_id["DQ-14"]["usage"]["calls"] == 1 and record["model"] == "scripted"


def test_the_budget_stops_the_run_before_a_point_not_during_it() -> None:
    ids = ("DQ-01", "DQ-02", "DQ-03", "DQ-04")
    record = run(
        _subset(*ids),
        models=build_scenario_models,
        label="scripted",
        usage=_Meter("0.01"),
        budget_usd=Decimal("0.04"),
    )
    assert record["not_run"], "the budget should have stopped the run"
    ran = [p["id"] for p in record["points"] if "not_run" not in p]
    assert record["summary"]["overall"]["n"] == len(ran) < len(ids)
    assert record["budget_usd"] == "0.04"


def test_a_paid_run_is_refused_without_its_confirmations() -> None:
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "decision_quality.py"
    spec = importlib.util.spec_from_file_location("dq_script", path)
    assert spec and spec.loader
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    def args(**kw: Any) -> argparse.Namespace:
        return argparse.Namespace(
            **{"decider": "haiku", "cap": Decimal("0.10"), "yes_paid": True, **kw}
        )

    assert "--yes-paid" in script._refusal(args(yes_paid=False))
    assert "--cap" in script._refusal(args(cap=None))
    assert "--cap" in script._refusal(args(cap=Decimal("5")))  # above the hard maximum


def test_the_meter_adds_up_one_call_log_per_point(tmp_path: Any) -> None:
    """A paid run keeps one call log per replayed point (fake case ids restart in every
    replay, so one shared log hit the per-case call limit); the meter sums them all."""
    import sqlite3

    from dispute_agent.api.decision_quality import meter

    for n, (micros, model) in enumerate([(1255, "claude-haiku-5-5"), (914, "claude-haiku-5-5")]):
        with sqlite3.connect(tmp_path / f"calls-{n:03d}.sqlite3") as db:
            db.execute(
                "create table model_calls (cost_usd_micros int, input_tokens int, "
                "output_tokens int, latency_ms int, model_id text, prompt_name text, "
                "prompt_version text, prompt_hash text)"
            )
            db.execute(
                "insert into model_calls values "
                "(?, 100, 10, 2000, ?, 'case_investigator_agent', 'v3', 'sha256:abc')",
                (micros, model),
            )
    found = meter(tmp_path)()
    assert found["calls"] == 2 and found["cost_usd"] == Decimal("0.002169")
    assert found["latency_ms"] == 4000 and found["models"] == ["claude-haiku-5-5"]
    assert found["prompts"] == ["case_investigator_agent v3"]
    assert found["prompt_hashes"] == ["sha256:abc"]  # the text, not only the version name
