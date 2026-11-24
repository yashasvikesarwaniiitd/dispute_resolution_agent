"""Decision Quality replays: each golden decision point, played offline, its proposal graded.

Every decision point is a short script on the scenario runner - the dispute is raised, then the
hub's or the seller's answer arrives - so the agent reaches that point with exactly the evidence
it would have. Fakes only: no Gmail, no Jira, a simulated ledger, a fake clock (ADR 0064).

Which reasoner decides is the caller's: the rules baseline (free), or a model (paid, step 3).
The proposal read back is the one recorded for the decision point's round; a credit's amount is
code's, from the credit intent that proposal planned.

A fair comparison needs the same evidence at the graded point for every decider. So for a
later-round point the rules decide the earlier rounds and the decider only the graded one
(`AtRound`): a model that asked the hub where the rules asked the seller would otherwise reach
a different round 2, or none.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

from dispute_agent.api import scenario_runner as runner
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.values import Money
from dispute_agent.modules.evals.decision_quality import (
    GRADER_VERSION,
    RUNS,
    Calibration,
    CalibrationItem,
    Golden,
    Graded,
    agreement,
    evidence_for,
    grade,
    load_calibration,
    load_golden,
    summary,
    write_index,
)
from dispute_agent.modules.evals.scenarios.catalogue import Scenario, script
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.modules.evals.decision_quality import DecisionPoint

#: Stop before a point when less than this is left of the run's budget (one decision on Haiku
#: has cost about USD 0.0013; a point with lookups can cost a few times that).
MARGIN_USD = Decimal("0.02")
#: The same for the judge, whose calls are small (about USD 0.001 each on Haiku).
JUDGE_MARGIN_USD = Decimal("0.003")

RAISED = "dispute_raised@0h"
#: How each later-round decision point is reached. Round 1 is the dispute alone.
REPLAY: dict[str, tuple[str, ...]] = {
    "DQ-11": (RAISED, "ops_finding(tray_mode_corrected)@30h"),
    "DQ-12": (RAISED, "ops_finding(no_fault_found)@30h"),
    "DQ-13": (RAISED, "ops_finding(declared_size_confirmed)@30h"),
    "DQ-14": (RAISED, "ops_finding(seller_is_right_unreadable)@30h"),
    "DQ-15": (RAISED, "ops_finding(inconclusive_still_checking)@30h"),
    "DQ-16": (RAISED, "ops_finding(approval_bypass_note)@30h"),
    "DQ-17": (RAISED, "approve(evidence_request)@1h", "seller_reply(photo_shows_large_box)@20h"),
    "DQ-18": (RAISED, "approve(evidence_request)@1h", "seller_reply(insists_no_photo)@20h"),
}


def rules_only(factory: Any) -> Any:
    """`factory`'s scenario world with the reasoner off: the rules baseline decides (free).

    The factory comes from the caller (app.build_scenario_models): only an entry point may
    import the composition root (tests/architecture/test_composition_root.py).
    """

    def built(model: Any, policy: Any, clock: Any, ids: Any) -> Any:
        return dataclasses.replace(factory(model, policy, clock, ids), reasoner=None)

    return built


@dataclasses.dataclass
class AtRound:
    """Implements Reasoner: the decider at the graded round, the rules before it."""

    decider: Any
    rules: Any
    graded_round: int

    def propose(self, briefing: CaseBriefing) -> Proposal:
        if briefing.evidence.gather_round < self.graded_round:
            return self.rules.propose(briefing)  # type: ignore[no-any-return]
        return self.decider.propose(briefing)  # type: ignore[no-any-return]


def at_round(factory: Any, graded_round: int) -> Any:
    """`factory`'s models, with its reasoner deciding only from `graded_round` on."""

    def built(model: Any, policy: Any, clock: Any, ids: Any) -> Any:
        models = factory(model, policy, clock, ids)
        rules = RulesOnlyReasoner(pack=WeightDisputePack(), ids=ids, clock=clock)
        return dataclasses.replace(models, reasoner=AtRound(models.reasoner, rules, graded_round))

    return built


SUMS = (
    "select count(*), coalesce(sum(cost_usd_micros), 0), coalesce(sum(input_tokens), 0), "
    "coalesce(sum(output_tokens), 0), coalesce(sum(latency_ms), 0) from model_calls"
)


def meter(log: Path) -> Callable[[], dict[str, Any]]:
    """Totals so far from a run's model-call logs (one file, or every `*.sqlite3` in a folder):
    calls, cost, tokens, latency, versions."""

    def read() -> dict[str, Any]:
        found: dict[str, Any] = {
            "calls": 0,
            "cost_usd": Decimal(0),
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": 0,
            "models": [],
            "prompts": [],
            "prompt_hashes": [],
        }
        files = sorted(log.glob("*.sqlite3")) if log.is_dir() else [log] if log.exists() else []
        micros, models, prompts, hashes = 0, set(), set(), set()
        for file in files:
            with sqlite3.connect(file) as db:
                calls, cost, tokens_in, tokens_out, ms = db.execute(SUMS).fetchone()
                models |= {m for (m,) in db.execute("select model_id from model_calls") if m}
                prompts |= {
                    f"{name} {version}"
                    for name, version in db.execute(
                        "select prompt_name, prompt_version from model_calls"
                    )
                    if name
                }
                hashes |= {h for (h,) in db.execute("select prompt_hash from model_calls") if h}
            micros += cost
            found["calls"] += calls
            found["tokens_in"] += tokens_in
            found["tokens_out"] += tokens_out
            found["latency_ms"] += ms
        found.update(
            cost_usd=Decimal(micros) / 1_000_000,
            models=sorted(models),
            prompts=sorted(prompts),
            prompt_hashes=sorted(hashes),
        )
        return found

    return read


@dataclasses.dataclass(frozen=True)
class Decided:
    """What the agent proposed at a decision point, or why the point was not reached."""

    action: str | None
    reason_code: str | None = None
    amount_inr: Decimal | None = None
    rationale: str = ""
    reasoner: str = ""
    fell_back: bool = False
    not_reached: str = ""


def replay(point: DecisionPoint, models: Any) -> Decided:
    """Play the decision point offline and read back the proposal of its round."""
    steps = script(list(REPLAY.get(point.id, (RAISED,))))
    scenario = Scenario(
        id=point.id,
        title=point.situation,
        phase=5,
        fixture=point.fixture,
        config={},
        faults={},
        steps=steps,
        expect={},
    )
    world = runner._run(scenario, None, models).world
    try:
        with world.engine.connect() as c:
            rows = c.execute(
                text(
                    "select id, action, proposal_json, reasoner, fallback_used from proposals "
                    "where gather_round = :r and reasoner != 'human' order by created_at"
                ),
                {"r": point.round},
            ).all()
            if not rows:
                reached = c.execute(text("select max(gather_round) from proposals")).scalar()
                return Decided(
                    None, not_reached=f"round {point.round} not reached (last {reached})"
                )
            proposal_id, action, body, reasoner, fell_back = rows[-1]
            credit = c.execute(
                text(
                    "select amount_paise from action_intents "
                    "where action_type = 'POST_CREDIT' and source_ref = :p"
                ),
                {"p": proposal_id},
            ).scalar()
    finally:
        world.close()
    parsed = json.loads(body)
    return Decided(
        action=str(action),
        reason_code=(parsed.get("action_parameters") or {}).get("reason_code"),
        amount_inr=None if credit is None else Money.from_paise(int(credit)).amount,
        rationale=str(parsed.get("rationale", "")),
        reasoner=str(reasoner),
        fell_back=bool(fell_back),
    )


def run(
    golden: Golden | None = None,
    *,
    models: Any,
    label: str = "rules",
    usage: Callable[[], dict[str, Any]] | None = None,
    budget_usd: Decimal | None = None,
) -> dict[str, Any]:
    """Every decision point replayed and graded, as one reproducible run record.

    With `usage` (a run's model-call meter) the decider decides only the graded round, and each
    point records its calls, cost, tokens and latency. With `budget_usd` the run stops before a
    point once less than MARGIN_USD is left; points it did not run are listed, never graded.
    """
    golden = golden or load_golden()
    points: list[dict[str, Any]] = []
    graded: list[Graded] = []
    for point in golden.decision_points:
        before = usage() if usage else None
        if budget_usd is not None and before and before["cost_usd"] > budget_usd - MARGIN_USD:
            points.append({"id": point.id, "not_run": f"budget: USD {before['cost_usd']} spent"})
            continue
        decided = replay(point, at_round(models, point.round) if usage else models)
        cost = _delta(before, usage()) if usage and before else None
        if decided.action is None:
            points.append({"id": point.id, "not_reached": decided.not_reached, "usage": cost})
            continue
        result = grade(
            point, decided.action, reason_code=decided.reason_code, amount_inr=decided.amount_inr
        )
        graded.append(result)
        points.append(
            {
                "id": point.id,
                "shipment": point.shipment,
                "round": point.round,
                "category": point.category,
                "action": decided.action,
                "reason_code": decided.reason_code,
                "amount_inr": None if decided.amount_inr is None else str(decided.amount_inr),
                "grade": result.grade.value,
                "problems": list(result.problems),
                "reasoner": decided.reasoner,
                "fell_back": decided.fell_back,
                "usage": cost,
                "rationale": decided.rationale,
                # Reserved for the LLM judge (step 4): filled in, never required.
                "judge": None,
            }
        )
    now = datetime.now(UTC)
    totals = usage() if usage else None
    return {
        "run_id": f"dq-{label}-{now:%Y%m%dT%H%M%SZ}",
        "created_at": now.isoformat(),
        "decider": label,
        "git_commit": _commit(),
        "golden_version": golden.version,
        "golden_digest": golden.digest,
        "grader_version": GRADER_VERSION,
        "model": ", ".join(totals["models"]) if totals else None,
        "prompt_version": ", ".join(totals["prompts"]) if totals else None,
        # The text's hash too: a prompt edited before activation keeps its version name.
        "prompt_hashes": totals.get("prompt_hashes", []) if totals else [],
        "cost_usd": str(totals["cost_usd"]) if totals else "0",
        "budget_usd": None if budget_usd is None else str(budget_usd),
        "model_calls": totals["calls"] if totals else 0,
        "tokens": {"in": totals["tokens_in"], "out": totals["tokens_out"]} if totals else None,
        "latency_ms_per_point": sorted(
            p["usage"]["latency_ms"] for p in points if p.get("usage") and p["usage"]["calls"]
        ),
        "fell_back": [p["id"] for p in points if p.get("fell_back")],
        "summary": summary(graded, golden),
        "not_reached": [p["id"] for p in points if "not_reached" in p],
        "not_run": [p["id"] for p in points if "not_run" in p],
        "points": points,
    }


def calibrate(
    judge: Any,
    *,
    golden: Golden | None = None,
    calibration: Calibration | None = None,
    usage: Callable[[], dict[str, Any]] | None = None,
    budget_usd: Decimal | None = None,
) -> dict[str, Any]:
    """The judge on every calibration item, compared with the owner's labels (step 4).

    The judge sees the decision point's evidence, the action and the explanation - never the
    label. An item the judge could not answer is recorded as an error and left out of the
    agreement, never counted as a pass.
    """
    golden = golden or load_golden()
    calibration = calibration or load_calibration()
    items: list[dict[str, Any]] = []
    judged: list[tuple[CalibrationItem, str]] = []
    for item in calibration.items:
        before = usage() if usage else None
        if budget_usd is not None and before and before["cost_usd"] > budget_usd - JUDGE_MARGIN_USD:
            items.append({"id": item.id, "not_run": f"budget: USD {before['cost_usd']} spent"})
            continue
        evidence = [*evidence_for(golden.point(item.point), golden), *item.extra_evidence]
        try:
            found = judge.judge(
                evidence=evidence,
                action=item.action,
                explanation=item.explanation,
                ref=item.id,
                credit=item.credit,
            )
        except KernelError as failed:
            # The reason too: "InvalidModelOutput" alone hid that 12 answers were cut short.
            items.append({"id": item.id, "error": f"{type(failed).__name__}: {str(failed)[:160]}"})
            continue
        judged.append((item, found.verdict))
        items.append(
            {
                "id": item.id,
                "point": item.point,
                "source": item.source,
                "owner": item.label.verdict,
                "judge": found.verdict,
                "agrees": found.verdict == item.label.verdict,
                "critical": item.label.critical,
                "unsupported_claims": [c.model_dump() for c in found.unsupported_claims],
                "suspicion_as_fact": found.suspicion_as_fact,
                "suspicion_detail": found.suspicion_detail,
                "action_reasonable": found.action_reasonable,
                "action_note": found.action_note,
                "usage": _delta(before, usage()) if usage and before else None,
            }
        )
    now = datetime.now(UTC)
    totals = usage() if usage else None
    return {
        "run_id": f"dq-judge-{now:%Y%m%dT%H%M%SZ}",
        "created_at": now.isoformat(),
        "git_commit": _commit(),
        "calibration_version": calibration.version,
        "calibration_status": calibration.status,
        "calibration_digest": calibration.digest,
        "golden_digest": golden.digest,
        "model": ", ".join(totals["models"]) if totals else None,
        "prompt_version": ", ".join(totals["prompts"]) if totals else None,
        "prompt_hashes": totals.get("prompt_hashes", []) if totals else [],
        "cost_usd": str(totals["cost_usd"]) if totals else "0",
        "budget_usd": None if budget_usd is None else str(budget_usd),
        "agreement": agreement(judged),
        "errors": [i["id"] for i in items if "error" in i],
        "not_run": [i["id"] for i in items if "not_run" in i],
        "items": items,
    }


def _delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "calls": after["calls"] - before["calls"],
        "cost_usd": str(after["cost_usd"] - before["cost_usd"]),
        "tokens_in": after["tokens_in"] - before["tokens_in"],
        "tokens_out": after["tokens_out"] - before["tokens_out"],
        "latency_ms": after["latency_ms"] - before["latency_ms"],
    }


def save(record: dict[str, Any], folder: Path = RUNS) -> Path:
    """Keep the run where the repository tracks it, and refresh the index (step 5)."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{record['run_id']}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    write_index(folder)
    return path


def _commit() -> str:
    """The commit that produced a run, "-dirty" when tracked files differ from it: a run made
    from uncommitted code is not reproducible from the commit alone."""
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=False
        )
        dirty = subprocess.run(["git", "diff", "--quiet", "HEAD"], check=False).returncode != 0
    except OSError:
        return "unknown"
    found = head.stdout.strip() or "unknown"
    return f"{found}-dirty" if dirty and found != "unknown" else found


__all__ = [
    "REPLAY",
    "AtRound",
    "Decided",
    "at_round",
    "calibrate",
    "meter",
    "replay",
    "rules_only",
    "run",
    "save",
]
