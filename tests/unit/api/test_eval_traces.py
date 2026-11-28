"""ADR 0074: evaluation runs in Langfuse - the mapping, and an import that is safe to repeat.

Offline: a fake Langfuse stands in for the REST API and the OTLP exporter.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dispute_agent.api.eval_traces import QUEUE, LangfuseEvalExport, build, spans
from dispute_agent.modules.evals.investigation import load_cases

RUN3 = (
    Path(__file__).resolve().parents[3]
    / "evals"
    / "investigation"
    / "runs"
    / "inv-focused-paid-20261127T163724Z.json"
)
CASES = {c.id: c.model_dump() for c in load_cases().cases}


@pytest.fixture(scope="module")
def run3() -> list[Any]:
    record = json.loads(RUN3.read_text(encoding="utf-8"))
    return build(record, CASES, None, run_id="run3")


def test_every_execution_of_run_3_becomes_one_trace_with_a_stable_id(run3: list[Any]) -> None:
    record = json.loads(RUN3.read_text(encoding="utf-8"))
    again = build(record, CASES, None, run_id="run3")
    assert len(run3) == 30
    assert {(e.case, e.arm) for e in run3} == {(r["case"], r["arm"]) for r in record["results"]}
    assert [e.trace_id for e in run3] == [e.trace_id for e in again]
    assert len({e.trace_id for e in run3}) == 30


def test_what_was_not_recorded_is_marked_unavailable(run3: list[Any]) -> None:
    agent = next(e for e in run3 if e.arm == "agent" and any(c.kind == "tool" for c in e.children))
    tool = next(c for c in agent.children if c.kind == "tool")
    assert str(tool.input).startswith("Not saved by this run: what it asked for")
    for e in run3:
        assert "prompts sent to the model" in e.metadata["unavailable"]
        assert e.root.input["What the agent was shown"].startswith("Not saved by this run")
        for gen in (c for c in e.children if c.kind == "generation"):
            assert str(gen.input).startswith("Not saved by this run")
            assert gen.usage is None  # the call logs were not given: no invented tokens


def test_steps_tool_calls_rejections_and_failures_are_each_an_observation(run3: list[Any]) -> None:
    enriched = next(e for e in run3 if (e.case, e.arm) == ("IC-01", "enriched"))
    kinds = [c.kind for c in enriched.children]
    assert kinds[:3] == ["tool", "tool", "tool"]  # code's look-ups, before the model
    assert all(c.metadata["chosen_by"] == "code" for c in enriched.children[:3])
    checks = [c for c in enriched.children if c.kind == "guardrail"]
    assert [c.metadata["verdict"] for c in checks] == ["rejected", "accepted"]
    assert checks[0].level == "WARNING" and "patterns" in checks[0].status
    assert "decision-rejected" in enriched.tags


def test_failures_and_unnecessary_contacts_are_filterable_and_queued(run3: list[Any]) -> None:
    wrong = [e for e in run3 if "incorrect" in e.tags]
    assert {(e.case, e.arm) for e in wrong} >= {("IC-15", "enriched"), ("IC-20", "investigator")}
    for e in wrong:
        assert e.review and any(s["name"] == "correct" and s["value"] == 0 for s in e.scores)
    seller = [e for e in run3 if "unnecessary-seller-request" in e.tags]
    assert seller and all(e.review for e in seller)
    assert all(e.session == f"run3:{e.case}" for e in run3)


def test_spans_carry_langfuse_trace_session_and_experiment_fields(run3: list[Any]) -> None:
    e = run3[0]
    made = spans(e, "dataset-1", {"langfuse.experiment.name": "run3 · agent"})
    root = made[0]
    assert root.parent is None and all(s.parent == root.context for s in made[1:])
    attrs = root.attributes
    assert attrs["langfuse.trace.name"] == "Case IC-01 · current agent (v3)"
    assert "case:IC-01" in attrs["langfuse.trace.tags"]
    assert attrs["langfuse.session.id"] == "run3:IC-01"
    assert attrs["langfuse.environment"] == "evaluation"
    assert attrs["langfuse.experiment.item.root_observation_id"] == format(
        root.context.span_id, "016x"
    )
    assert f"{root.context.trace_id:032x}" == e.trace_id


def test_a_reviewer_reads_plain_steps_and_a_plain_decision(run3: list[Any]) -> None:
    enriched = next(e for e in run3 if (e.case, e.arm) == ("IC-01", "enriched"))
    names = [c.name for c in enriched.children]
    assert all(n.startswith(f"Step {k}: ") for k, n in enumerate(names, 1))
    assert names[0].startswith("Step 1: Look up past shipments of this product (done automatically")
    assert any("the agent makes its decision" in n for n in names)
    assert any(n.endswith("Rule check of the decision: rejected") for n in names)
    assert not any("_" in n for n in names)  # no code names
    check = next(c for c in enriched.children if c.kind == "guardrail")
    assert check.output == "Rejected: claims to have considered patterns, but none were shown"
    decided = run3[0].root.output
    assert decided["Decision"] == "Give the seller a credit" and decided["Graded"] == "Correct"
    assert decided["What it found"][0].startswith("Verified fact: ")


def test_a_rebuilt_first_prompt_is_shown_and_labelled_and_later_ones_are_not() -> None:
    record = json.loads(RUN3.read_text(encoding="utf-8"))
    for r in record["results"]:
        r["first_prompt"] = {"system": "the instructions", "user": f"the case {r['case']}"}
    e = next(
        x
        for x in build(record, CASES, None, run_id="run3")
        if x.case == "IC-01" and x.arm == "enriched"
    )
    assert e.root.input["What the agent was shown"] == "the case IC-01"
    first, *later = [c for c in e.children if c.kind == "generation"]
    assert first.input["Its instructions (the same for every case)"] == "the instructions"
    assert first.input["How we know"].startswith("Rebuilt after the run")
    assert later and all(str(g.input).startswith("Not saved by this run") for g in later)
    assert "prompts after the first (the first was rebuilt)" in e.metadata["unavailable"]
    assert first.input["Exact text the agent was shown"] == "the case IC-01"


def test_what_the_agent_was_shown_is_sorted_into_plain_categories() -> None:
    from dispute_agent.api.eval_traces import readable_prompt

    rebuilt = json.loads(RUN3.with_suffix(".inputs.json").read_text(encoding="utf-8"))
    shown = readable_prompt(rebuilt["IC-20/investigator"]["user"])
    assert list(shown)[:3] == [
        "1. The dispute",
        "2. The bill, as checked by code",
        "3. Evidence on file",
    ]
    assert shown["1. The dispute"]["Shipment"] == "SHP-104904"
    bill = shown["2. The bill, as checked by code"]
    assert (bill["Amount charged"], bill["Overcharge found"]) == ("₹114.00", "₹0.00")
    assert bill["Measured by the hub"] == "length 67.1, width 8.9, height 9, weight 210 g"
    assert "this parcel's scan and tracking history" in shown["3. Evidence on file"]["Missing"]
    assert "Send the case to a person" in shown["7. What the agent may decide"]
    assert shown["9. Limits"] == {
        "Steps left": "5",
        "Paid checks left": "1",
        "Must decide now": "no",
    }
    assert "fact_id" not in json.dumps(shown)
    seller = readable_prompt(rebuilt["IC-15/enriched"]["user"])
    assert seller["2. What the seller wrote (not verified)"]["From"] == "dispute form"
    for prompt in rebuilt.values():  # every section of every prompt lands in one category
        assert isinstance(readable_prompt(prompt["user"]), dict)
    assert readable_prompt("free text") == "free text"  # unrecognised: shown as it is


class FakeLangfuse:
    """The REST endpoints the import uses, in memory."""

    def __init__(self) -> None:
        self.datasets: dict[str, dict[str, Any]] = {}
        self.items: dict[str, Any] = {}
        self.scores: dict[str, Any] = {}
        self.configs: list[dict[str, Any]] = []
        self.queues: list[dict[str, Any]] = []
        self.queue_items: dict[str, list[dict[str, Any]]] = {}
        self.deleted: list[str] = []

    @staticmethod
    def reply(status: int, body: Any = None) -> Any:
        return SimpleNamespace(status_code=status, json=lambda: body, content=b"x")

    def get(self, path: str, params: Any = None) -> Any:
        if path.startswith("/v2/datasets/"):
            name = path.rsplit("/", 1)[1]
            return (
                self.reply(200, self.datasets[name]) if name in self.datasets else self.reply(404)
            )
        if path == "/score-configs":
            return self.reply(200, {"data": self.configs})
        if path == "/annotation-queues":
            return self.reply(200, {"data": self.queues})
        if path.endswith("/items"):
            qid = path.split("/")[2]
            page = (params or {}).get("page", 1)
            items = self.queue_items.get(qid, [])
            return self.reply(200, {"data": items[(page - 1) * 100 : page * 100]})
        raise AssertionError(path)

    def post(self, path: str, json: Any) -> Any:
        if path == "/v2/datasets":
            self.datasets[json["name"]] = {"id": "ds-1", **json}
            return self.reply(200, self.datasets[json["name"]])
        if path == "/dataset-items":
            self.items[json["id"]] = json
            return self.reply(200, json)
        if path == "/scores":
            for s in json:
                self.scores[s["id"]] = s  # upsert by id
            return self.reply(202, {"message": "Accepted"})
        if path == "/score-configs":
            made = {"id": f"cfg-{len(self.configs)}", **json}
            self.configs.append(made)
            return self.reply(200, made)
        if path == "/annotation-queues":
            made = {"id": f"q-{len(self.queues)}", **json}
            self.queues.append(made)
            return self.reply(200, made)
        if path.endswith("/items"):
            qid = path.split("/")[2]
            made = {
                "id": f"item-{len(self.queue_items.get(qid, []))}-{json['objectId'][:6]}",
                **json,
            }
            self.queue_items.setdefault(qid, []).append(made)
            return self.reply(200, made)
        raise AssertionError(path)


def _request(self: Any, method: str, path: str, json: Any = None) -> Any:
    assert method == "DELETE"
    if path == "/traces":
        self.deleted.extend(json["traceIds"])
        return self.reply(200, {"message": "accepted"})
    qid, item = path.split("/")[2], path.split("/")[4]
    self.queue_items[qid] = [i for i in self.queue_items[qid] if i.get("id") != item]
    return self.reply(200, {})


FakeLangfuse.request = _request  # type: ignore[attr-defined]


class FakeOtlp:
    def __init__(self) -> None:
        self.batches: list[list[Any]] = []

    def export(self, batch: list[Any]) -> Any:
        self.batches.append(batch)
        return SimpleNamespace(name="SUCCESS")


def test_the_import_can_be_repeated_without_duplicates(run3: list[Any], tmp_path: Path) -> None:
    http, otlp = FakeLangfuse(), FakeOtlp()
    export = LangfuseEvalExport("https://x.invalid", "pk", "sk", http=http, otlp=otlp)
    manifest = tmp_path / "manifest.json"
    first = export.send(run3, manifest, label="run3")
    assert (first.traces_sent, first.traces_skipped, first.problems) == (30, 0, [])
    assert first.queue_items_added == sum(e.review for e in run3)
    scores, items = dict(http.scores), dict(http.items)

    second = export.send(run3, manifest, label="run3")
    assert (second.traces_sent, second.traces_skipped, second.queue_items_added) == (0, 30, 0)
    assert len(otlp.batches) == 30  # no trace sent twice
    assert http.scores == scores and http.items == items  # upserted, not duplicated
    assert [q["name"] for q in http.queues] == [QUEUE]
    assert [c["name"] for c in http.configs] == ["failure_category", "failure_notes"]
    assert len(http.items) == 10  # one dataset item per case


def test_a_single_arm_run_record_imports_too_with_what_it_lacks_marked() -> None:
    record = {
        "label": "investigator",
        "ran_at": "2026-12-01T10:00:00+00:00",
        "cases": [
            {
                "id": "IC-09",
                "category": "refined_query",
                "action": "request_seller_evidence",
                "grade": "acceptable",
                "trajectory": ["search_past_shipments", "decide"],
                "cited": ["T1.1"],
            }
        ],
    }
    (e,) = build(record, CASES, None, run_id="later")
    assert (e.case, e.arm) == ("IC-09", "investigator") and "correct" in e.tags
    tool = next(c for c in e.children if c.kind == "tool")
    assert str(tool.input).startswith("Not saved by this run")
    assert "prompts sent to the model" in e.metadata["unavailable"]


def test_a_trace_already_sent_is_never_sent_again_even_if_it_changed(
    run3: list[Any], tmp_path: Path
) -> None:
    import dataclasses

    http, otlp = FakeLangfuse(), FakeOtlp()
    export = LangfuseEvalExport("https://x.invalid", "pk", "sk", http=http, otlp=otlp)
    manifest = tmp_path / "manifest.json"
    export.send(run3[:1], manifest, label="run3")
    changed = dataclasses.replace(run3[0], tags=(*run3[0].tags, "edited"))
    again = export.send([changed], manifest, label="run3")
    assert again.traces_sent == 0 and again.changed_not_resent == ["IC-01/agent"]
    assert len(otlp.batches) == 1


def test_replacing_deletes_then_sends_once_under_new_ids(tmp_path: Path) -> None:
    from dispute_agent.api.eval_traces import reissued

    record = json.loads(RUN3.read_text(encoding="utf-8"))
    first = build(record, CASES, None, run_id="run3")
    http, otlp = FakeLangfuse(), FakeOtlp()
    export = LangfuseEvalExport("https://x.invalid", "pk", "sk", http=http, otlp=otlp)
    manifest = tmp_path / "manifest.json"
    export.send(first, manifest, label="run3")
    wrong = [e for e in first if e.review][:2]
    assert export.delete(wrong, manifest) == []
    assert http.deleted == [e.trace_id for e in wrong]
    queued = {i["objectId"] for q in http.queue_items.values() for i in q}
    assert not queued & {e.trace_id for e in wrong}  # their review items went with them

    second = build(record, CASES, None, run_id="run3", reissued=reissued(manifest))
    renamed = {(e.case, e.arm): e.trace_id for e in second}
    for e in wrong:
        assert renamed[(e.case, e.arm)] != e.trace_id  # never the id being deleted
    report = export.send(second, manifest, label="run3")
    assert (report.traces_sent, report.traces_skipped) == (2, 28)
    third = export.send(
        build(record, CASES, None, run_id="run3", reissued=reissued(manifest)),
        manifest,
        label="run3",
    )
    assert third.traces_sent == 0
