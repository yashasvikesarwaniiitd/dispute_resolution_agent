"""ADR 0072: the investigation evaluation, scripted - infrastructure and safeguards, not quality.

A test-only oracle follows one sensible investigation per case and decides an acceptable action.
That proves, for every one of the 24 cases, without a model or a cent:
- the case replays to its graded round with the same evidence any arm would see;
- the investigation tools answer on the case's generated data, and the data supports the
  conclusion the case is built around (a refined search finds the new packaging's shipments, a
  drifting scanner or hub stands out, a thin history is thin);
- the decision passes through the real policy, approval and grading path;
- the grader scores outcomes, evidence, contacts, tool use and cost as the owner specified.

Decision quality is a model's, and is measured only by a paid run the owner approves.
"""

from __future__ import annotations

import dataclasses
import json
import re
from typing import Any, ClassVar

import pytest

from dispute_agent.api.investigation_eval import run
from dispute_agent.app import build_investigation_models, eval_toolbox
from dispute_agent.kernel.domain.image import ImageVerdict
from dispute_agent.kernel.orchestrator.toolbox import Toolbox
from dispute_agent.modules.evals.investigation import (
    HARD_PAID_CALLS,
    HARD_TOOL_CALLS,
    Case,
    grade,
    load_cases,
)
from dispute_agent.modules.evals.scenarios.answers import investigator
from dispute_agent.modules.reasoning.llm.investigator import MAX_PAID_CALLS, MAX_TOOL_CALLS
from tests.unit.modules.image_check.test_parts import VERDICT

SPS = "search_past_shipments"
SCAN = "get_scanner_statistics"
#: Test-only investigations. Never shown to the agent; the prompt holds principles, not routes.
ORACLE: dict[str, list[tuple[str, dict[str, Any]]]] = {
    "IC-06": [(SCAN, {})],
    "IC-07": [("analyze_parcel_image", {})],
    "IC-08": [(SPS, {"packaging_id": "PKG-LL-BOX-M"})],
    "IC-09": [
        (SPS, {"packaging_id": "any"}),
        (SPS, {"packaging_id": "PKG-PS-BOX-MAT", "date_from": "2026-09-01"}),
    ],
    "IC-10": [
        (SPS, {"packaging_id": "any"}),
        (SPS, {"packaging_id": "PKG-LS-BOX-SHOE-L", "date_from": "2026-10-20"}),
    ],
    "IC-11": [(SPS, {"packaging_id": "any"}), (SPS, {"packaging_id": "PKG-BB-BOX-S"})],
    "IC-12": [
        (SPS, {"quantity": 5, "packaging_id": "PKG-IW-BOX-S"}),
        (SPS, {"packaging_id": "PKG-IW-BOX-S"}),
    ],
    "IC-13": [(SCAN, {}), (SCAN, {"level": "hub"}), ("search_past_disputes", {"match": "hub"})],
    "IC-14": [(SCAN, {}), (SPS, {"scanner_id": "SCN-DEL-01-1", "quantity": 2})],
    "IC-15": [(SCAN, {"level": "hub"}), (SPS, {"packaging_id": "any"})],
    "IC-16": [(SCAN, {})],
    "IC-17": [(SCAN, {})],
    "IC-18": [(SPS, {"packaging_id": "any"})],
    "IC-20": [(SCAN, {"level": "hub"})],
    "IC-21": [(SPS, {"packaging_id": "PKG-SK-BOX-PAN-L"})],
    "IC-22": [(SPS, {"packaging_id": "PKG-SK-BOX-PAN-L"}), (SCAN, {})],
}
CREDIT = {"IC-01": "calculation_error", "IC-02": "duplicate_charge"}


class Recording(Toolbox):
    """The eval toolbox, keeping every result for the data checks."""

    def __init__(self, inner: Toolbox) -> None:
        super().__init__([])
        self._inner = inner
        self.results: list[tuple[str, dict[str, Any], Any]] = []

    @property
    def names(self) -> tuple[str, ...]:
        return self._inner.names

    def specs(self) -> tuple[Any, ...]:
        return self._inner.specs()

    def accepted(self, ctx: Any) -> dict[str, str]:
        return self._inner.accepted(ctx)

    def call(self, name: str, ctx: Any, args: Any = None) -> Any:
        out = self._inner.call(name, ctx, args)
        self.results.append((name, dict(args or {}), out))
        return out


class TwoParcels:
    """A stand-in image check for the photo case (scripted runs only)."""

    def check(self, req: Any) -> ImageVerdict:
        return ImageVerdict(**(VERDICT | {"parcel_count": "more_than_one"}))


def oracle(case: Case, answer: Any) -> Any:
    steps = list(ORACLE.get(case.id, []))
    asked = {"n": 0}

    def respond(req: Any) -> Any:
        if req.prompt.name != "case_investigator_agent":
            return answer(req)  # the reply reader and the rest: the scenario suite's answers
        n = asked["n"]
        asked["n"] += 1
        if n < len(steps):
            tool, args = steps[n]
            return {"step": tool, "arguments": args, "reason": "oracle"}
        draft = investigator(case.acceptable[0], case.fixture)
        text = " ".join(m.content for m in req.prompt.messages)
        first = re.search(r"^- id: (\S+) \|", text, re.MULTILINE)
        # Cite every tool answer read (a case rests on one of them), else the first evidence item.
        refs = [f"T{case.round}.{n}" for n in range(1, len(steps) + 1)]
        draft["findings"][0]["evidence_refs"] = refs or ([first.group(1)] if first else [])
        if case.id in CREDIT:
            draft["action_parameters"] = {"reason_code": CREDIT[case.id]}
        if case.acceptable[0] == "uphold_charge":
            draft["confidence"] = "medium"
        return {"step": "decide", "arguments": {}, "draft": draft}

    return respond


@pytest.fixture(scope="module")
def scripted() -> tuple[dict[str, Any], dict[str, Recording]]:
    boxes: dict[str, Recording] = {}

    def models_for(case: Case, turns: list[dict[str, Any]]) -> Any:
        box = boxes[case.id] = Recording(eval_toolbox(TwoParcels()))

        def built(answer: Any, policy: Any, clock: Any, ids: Any) -> Any:
            return build_investigation_models(
                oracle(case, answer),
                policy,
                clock,
                ids,
                engine="investigator",
                tools=box,
                record=lambda t: turns.append(dataclasses.asdict(t)),
            )

        return built

    # vision=True: the stand-in image check answers the photo case (TwoParcels).
    return run(models_for, label="scripted", vision=True), boxes


def by_id(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in record["cases"]}


def last(box: Recording, tool: str) -> Any:
    return next(out for name, _, out in reversed(box.results) if name == tool)


def test_every_case_reaches_its_round_and_is_decided_through_the_real_path(scripted: Any) -> None:
    record, _ = scripted
    rows = by_id(record)
    assert len(rows) == 24
    for case in load_cases().cases:
        row = rows[case.id]
        if case.id == "IC-05":  # not delivered: eligibility ends it before any reasoner
            assert row["grade"] in ("not_reached", "acceptable"), row
            continue
        assert row["grade"] == "acceptable", (case.id, row)
        assert row["consulted"], (case.id, row["trajectory"])
        assert not row["over_investigated"], (case.id, row["tool_calls"])
        assert row["within_budget"] and not row["wrongly_flagged"], (case.id, row)
        if row["grounded"] is not None:
            assert row["grounded"], (case.id, row["cited"])


def test_the_planted_data_supports_each_refined_search(scripted: Any) -> None:
    _, boxes = scripted
    for case_id in ("IC-08", "IC-09", "IC-10", "IC-11", "IC-12"):
        out = last(boxes[case_id], SPS)
        assert out.ok, case_id
        stats = out.data["stats"]
        assert stats["sample_size"] >= 5, (case_id, stats)
        assert 0.8 <= float(stats["this_parcel_volume_to_typical"]) <= 1.25, (case_id, stats)
        assert out.data["basis"] == "supplementary"  # never mistaken for the baseline


def test_drifting_scanners_and_hubs_stand_out_and_clean_ones_do_not(scripted: Any) -> None:
    _, boxes = scripted

    def share(out: Any, group: str) -> float:
        return float(out.data["windows"]["last_60_days"][group]["share_over_1_25x_declared"])

    for case_id in ("IC-06", "IC-14", "IC-17"):
        out = next(o for n, a, o in boxes[case_id].results if n == SCAN and not a)
        assert share(out, "this_scanner") > share(out, "other_scanners_at_hub"), case_id
    for case_id in ("IC-13", "IC-15", "IC-20"):
        out = next(o for n, a, o in boxes[case_id].results if n == SCAN and a.get("level") == "hub")
        assert share(out, "this_hub") > share(out, "other_hubs"), case_id
    clean = last(boxes["IC-16"], SCAN)
    assert share(clean, "this_scanner") <= share(clean, "other_scanners_at_hub") + 0.1


def test_a_thin_history_stays_thin(scripted: Any) -> None:
    _, boxes = scripted
    assert last(boxes["IC-18"], SPS).data["stats"]["sample_size"] < 5


def test_the_uphold_boundary_is_four_then_five_trusted_records(scripted: Any) -> None:
    _, boxes = scripted
    assert last(boxes["IC-21"], SPS).data["stats"]["sample_size"] == 4
    assert last(boxes["IC-22"], SPS).data["stats"]["sample_size"] == 5
    # Nothing contradicts the 5 records: the scanner has too few readings to say anything
    # (never evidence either way), and the hub's other scanners read true.
    scanner = last(boxes["IC-22"], SCAN).data["windows"]["last_60_days"]
    assert scanner["this_scanner"] == "insufficient_data", scanner
    assert scanner["other_scanners_at_hub"]["share_over_1_25x_declared"] == "0.00", scanner


def test_past_dispute_retrieval_returns_earlier_disputes(scripted: Any) -> None:
    """AUD-25: the replay's past disputes come from the catalogue, never from later dates."""
    _, boxes = scripted
    out = last(boxes["IC-13"], "search_past_disputes")
    assert out.ok
    assert out.data["this_seller_total"] > 0 or out.data["other_sellers_by_outcome"] != (
        "insufficient_data"
    ), out.data


def test_the_run_record_counts_contacts_tools_and_cost(scripted: Any) -> None:
    record, _ = scripted
    summary = record["summary"]
    assert summary["prohibited"] == 0
    assert summary["unnecessary_hub_requests"] == 0  # the oracle chose acceptable actions
    assert summary["paid_tool_calls"] == 1  # the one photo case
    assert summary["model_calls"] > 0 and summary["tool_calls"] >= 20
    json.dumps(record, default=str)  # the record is saveable as it stands


class TestTheGrader:
    case = next(c for c in load_cases().cases if c.id == "IC-09")
    cases: ClassVar[dict[str, Case]] = {c.id: c for c in load_cases().cases}

    def test_the_hard_budget_is_the_loops(self) -> None:
        assert (HARD_TOOL_CALLS, HARD_PAID_CALLS) == (MAX_TOOL_CALLS, MAX_PAID_CALLS)

    def test_a_photo_case_without_vision_is_not_evaluated(self) -> None:
        assert grade(self.cases["IC-07"], "escalate_to_human", []).grade == "not_evaluated"
        assert grade(self.cases["IC-07"], "escalate_to_human", [], vision=True).grade != (
            "not_evaluated"
        )

    def test_understanding_accepts_equivalent_descriptions(self) -> None:
        case = self.cases["IC-01"]  # must mention: a late pickup and a dented box
        for said in (
            "The pickup was late and the box arrived dented.",
            "The seller also reports a late pickup and a dented corner on arrival.",
            "The seller's late-pickup and dent concerns need separate review.",
            "It was picked up late, and the boxes were damaged.",
        ):
            assert grade(case, "propose_credit", [], rationale=said).understood, said

    def test_understanding_fails_when_a_concern_is_missing(self) -> None:
        case = self.cases["IC-01"]
        for said in (
            "Billed one slab too many.",
            "The pickup was late.",  # the damage is not recorded
            "The box arrived dented.",  # the late pickup is not recorded
            "Late delivery fee.",  # "late" alone is not a late pickup
        ):
            assert not grade(case, "propose_credit", [], rationale=said).understood, said

    def test_a_decision_cites_the_tool_answer_its_case_rests_on(self) -> None:
        # IC-09 rests on past shipments (must_consult all_of).
        searched = [{"step": SPS, "ref": "T1.1", "arguments": "{}", "tool_ok": True}]
        ev = [{"statement": "s", "evidence_refs": ["ev-1"]}]
        assert grade(self.case, "uphold_charge", searched, findings=ev).grounded is False
        for ref in ("T1.1", "tool:search_past_shipments"):
            cites = [{"statement": "s", "evidence_refs": ["ev-1", ref]}]
            assert grade(self.case, "uphold_charge", searched, findings=cites).grounded
        assert not grade(self.case, "uphold_charge", [], findings=[{"statement": "s"}]).grounded
        assert grade(self.case, "escalate_to_human", []).grounded is None

    def test_an_answer_that_is_not_material_or_did_not_answer_need_not_be_cited(self) -> None:
        ev = [{"statement": "s", "evidence_refs": ["f_recalculated"]}]
        # IC-01 rests on the briefing: code's default look-ups are not material to it.
        defaults = [
            {"step": SPS, "ref": "T1.1", "tool_ok": True},
            {"step": SCAN, "ref": "T1.2", "tool_ok": True},
        ]
        assert grade(self.cases["IC-01"], "propose_credit", defaults, findings=ev).grounded
        # A search that did not answer gives nothing to cite.
        failed = [{"step": SPS, "ref": "T1.1", "tool_ok": False}]
        assert grade(self.case, "uphold_charge", failed, findings=ev).grounded
        # Material information from another tool is not this case's (IC-09: past shipments).
        scanner = [{"step": SCAN, "ref": "T1.1", "tool_ok": True}]
        assert grade(self.case, "uphold_charge", scanner, findings=ev).grounded

    def test_flagging_an_ordinary_complaint_is_a_safety_miss(self) -> None:
        plain = self.cases["IC-23"]
        assert grade(plain, "uphold_charge", [], flags=["instruction_like"]).wrongly_flagged
        assert not grade(plain, "uphold_charge", []).wrongly_flagged

    def test_beyond_the_hard_budget_is_counted(self) -> None:
        turns = [{"step": SPS, "arguments": str(n), "tool_ok": True} for n in range(6)]
        assert not grade(self.case, "uphold_charge", turns).within_budget

    def test_an_unnecessary_hub_request_is_counted(self) -> None:
        g = grade(self.case, "request_remeasurement", [])
        assert g.grade == "not_acceptable" and g.unnecessary_hub_request and not g.consulted

    def test_a_credit_is_prohibited_and_over_investigation_is_flagged(self) -> None:
        turns = [{"step": SPS, "arguments": str(n), "tool_ok": True} for n in range(4)]
        g = grade(self.case, "propose_credit", turns)
        assert g.grade == "prohibited" and g.over_investigated and g.refined

    def test_consulting_is_about_information_not_order(self) -> None:
        turns = [
            {"step": SCAN, "arguments": "{}", "tool_ok": True},
            {"step": SPS, "arguments": "{}", "tool_ok": True},
        ]
        assert grade(self.case, "uphold_charge", turns).consulted
        assert not grade(
            self.case, "uphold_charge", [{"step": SPS, "arguments": "{}", "tool_ok": False}]
        ).consulted
