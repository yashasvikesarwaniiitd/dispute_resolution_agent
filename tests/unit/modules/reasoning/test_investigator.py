"""The investigation agent (ADR 0072), on the real investigate station with a scripted model.

Only the model is scripted. These prove the infrastructure and the safeguards, not decision
quality (that needs real model runs, which need the owner's approval):
- zero tool calls is a normal investigation;
- a tool result reaches the next turn and can be cited;
- the per-round tool budget, the one paid check, and no repeated query are enforced in code;
- a stable result is reused in a later round instead of fetched again;
- the enriched arm runs code's look-ups and asks once;
- every turn is recorded with its tokens, cache use, latency and cost;
- the real tools, on a generated dispute, reach the model labelled and citable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.enums import CaseState, ReviewCode
from dispute_agent.kernel.domain.tools import ToolContext, ToolOutput, ToolSpec
from dispute_agent.kernel.orchestrator.toolbox import Toolbox
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from dispute_agent.modules.reasoning.llm.investigator import (
    MAX_TOOL_CALLS,
    PROMPT,
    VERSION,
    InvestigatorReasoner,
    Turn,
)
from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry
from tests.e2e.test_phase3_investigate import run
from tests.model_fixtures import PROMPTS, valid_answer

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path


@pytest.fixture
def engine(tmp_path: Path) -> Any:
    from dispute_agent.infra.sqlite.engine import (
        apply_schema,
        create_sqlite_engine,
        schema_sql_path,
    )

    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


class Counting:
    """A stand-in investigation tool that counts its calls."""

    def __init__(self, name: str, answer: dict[str, Any] | None = None) -> None:
        self.spec = ToolSpec(
            name=name,
            description=f"{name} for tests",
            input_schema={"type": "object", "properties": {}},
        )
        self.calls = 0
        self.answer = answer or {"found": f"{name} result"}

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        self.calls += 1
        return ToolOutput(tool=self.spec.name, ok=True, data=self.answer | {"args": dict(args)})


def decide(**draft: Any) -> dict[str, Any]:
    return {"step": "decide", "arguments": {}, "draft": valid_answer(**draft)}


def call(tool: str, **arguments: Any) -> dict[str, Any]:
    return {"step": tool, "arguments": arguments, "reason": "test"}


def investigator(
    gateway: FakeModelGateway, made: list[InvestigatorReasoner], tools: list[Any], **options: Any
) -> Any:
    def factory(ids: Any, clock: Any) -> InvestigatorReasoner:
        reasoner = InvestigatorReasoner(
            prompts=FilePromptRegistry(PROMPTS, allow_inactive=True),
            gateway=gateway,
            ids=ids,
            clock=clock,
            toolbox=Toolbox(tools),
            **options,
        )
        made.append(reasoner)
        return reasoner

    return factory


def last_prompt(gateway: FakeModelGateway) -> str:
    return str(gateway.requests[-1].prompt.messages[-1].content)


def steps(reasoner: InvestigatorReasoner) -> list[str]:
    return [t.step for t in reasoner.trace]


def test_a_clear_case_is_decided_with_no_tool_call(engine: Any) -> None:
    gateway = FakeModelGateway(script=[decide()])
    made: list[InvestigatorReasoner] = []
    case, approvals, *_ = run(
        engine, "scanner_fault", reasoner=investigator(gateway, made, [Counting("lookup_policy")])
    )
    assert case.state is CaseState.AWAITING_APPROVAL and len(approvals) == 1
    assert steps(made[0]) == ["decide"] and len(gateway.requests) == 1
    assert (gateway.requests[0].prompt.name, gateway.requests[0].prompt.version) == (
        PROMPT,
        VERSION,
    )


def test_a_tool_result_reaches_the_next_turn_and_can_be_cited(engine: Any) -> None:
    tool = Counting("search_past_shipments")
    cited = valid_answer()
    cited["findings"] = [
        {
            "kind": "verified_fact",
            "statement": "Past shipments match.",
            "evidence_refs": ["T1.1", "tool:search_past_shipments"],
        }
    ]
    gateway = FakeModelGateway(
        script=[
            call("search_past_shipments", packaging_id="any"),
            {"step": "decide", "arguments": {}, "draft": cited},
        ]
    )
    made: list[InvestigatorReasoner] = []
    case, *_ = run(engine, "scanner_fault", reasoner=investigator(gateway, made, [tool]))
    assert case.state is CaseState.AWAITING_APPROVAL
    assert tool.calls == 1 and steps(made[0]) == ["search_past_shipments", "decide"]
    assert "search_past_shipments result" in last_prompt(gateway)
    # An investigation turn carries no proposal: only the tool, its arguments and a reason.
    assert "draft" not in call("search_past_shipments")


def test_the_tool_budget_is_enforced_in_code(engine: Any) -> None:
    names = (
        "search_past_shipments",
        "get_scanner_statistics",
        "search_past_disputes",
        "lookup_policy",
    )
    tools = [Counting(n) for n in names]
    script: list[str | dict[str, object]] = []
    script.extend(call(n, ask=k) for k in range(2) for n in names)
    del script[MAX_TOOL_CALLS + 2 :]  # seven tool calls, never a decision
    gateway = FakeModelGateway(script=script)
    made: list[InvestigatorReasoner] = []
    case, *_ = run(engine, "scanner_fault", reasoner=investigator(gateway, made, tools))
    assert sum(t.calls for t in tools) == MAX_TOOL_CALLS  # the sixth and seventh never run
    assert len(gateway.requests) == MAX_TOOL_CALLS + 2  # warned once, then a person decides
    assert "<must_decide>true</must_decide>" in last_prompt(gateway)
    assert "not run: no tool calls are left in this round. Decide now" in last_prompt(gateway)
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert (
        case.review_reason is not None
        and case.review_reason.code is ReviewCode.INVALID_MODEL_OUTPUT
    )


def test_a_repeated_query_is_not_run_again(engine: Any) -> None:
    tool = Counting("get_scanner_statistics")
    gateway = FakeModelGateway(
        script=[call("get_scanner_statistics"), call("get_scanner_statistics"), decide()]
    )
    made: list[InvestigatorReasoner] = []
    run(engine, "scanner_fault", reasoner=investigator(gateway, made, [tool]))
    assert tool.calls == 1
    assert "same query as T1.1" in last_prompt(gateway)


def test_only_one_paid_check_per_round(engine: Any) -> None:
    image = Counting("analyze_parcel_image")
    gateway = FakeModelGateway(
        script=[call("analyze_parcel_image"), call("analyze_parcel_image", again=True), decide()]
    )
    made: list[InvestigatorReasoner] = []
    run(engine, "scanner_fault", reasoner=investigator(gateway, made, [image]))
    assert image.calls == 1
    assert "paid-check limit" in last_prompt(gateway)
    assert "<paid_checks_left>0</paid_checks_left>" in last_prompt(gateway)


def test_a_stable_result_is_reused_in_a_later_round(engine: Any) -> None:
    tool = Counting("search_past_shipments")
    gateway = FakeModelGateway(
        script=[call("search_past_shipments"), decide(), call("search_past_shipments"), decide()]
    )
    made: list[InvestigatorReasoner] = []
    seen: list[Any] = []

    def factory(ids: Any, clock: Any) -> Any:
        reasoner = investigator(gateway, made, [tool])(ids, clock)
        original = reasoner.propose

        def propose(briefing: Any) -> Any:
            seen.append(briefing)
            return original(briefing)

        reasoner.propose = propose
        return reasoner

    run(engine, "scanner_fault", reasoner=factory)
    made[0].propose(seen[0])  # the same case decides again: a later round
    assert tool.calls == 1
    assert "(reused from an earlier round)" in last_prompt(gateway)
    assert any(t.reused for t in made[0].trace)


def test_the_enriched_arm_runs_code_s_look_ups_and_asks_once(engine: Any) -> None:
    tools = [
        Counting("search_past_shipments"),
        Counting("get_scanner_statistics"),
        Counting("search_past_disputes"),
    ]
    gateway = FakeModelGateway(script=[decide()])
    made: list[InvestigatorReasoner] = []
    case, *_ = run(
        engine, "scanner_fault", reasoner=investigator(gateway, made, tools, agentic=False)
    )
    assert case.state is CaseState.AWAITING_APPROVAL
    assert len(gateway.requests) == 1 and all(t.calls == 1 for t in tools)
    prompt = last_prompt(gateway)
    assert all(f"T1.{n}:" in prompt for n in (1, 2, 3))
    assert "<must_decide>true</must_decide>" in prompt
    # Never offered tools it cannot call: its look-ups are its notes.
    assert "none in this run: code has already run the look-ups" in prompt
    assert "- search_past_shipments - arguments" not in prompt


def test_a_tool_call_after_the_budget_is_warned_once_then_the_decision_counts(
    engine: Any,
) -> None:
    gateway = FakeModelGateway(script=[call("lookup_policy"), decide()])
    made: list[InvestigatorReasoner] = []
    case, *_ = run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, made, [Counting("lookup_policy")], agentic=False),
    )
    assert case.state is CaseState.AWAITING_APPROVAL
    assert "not run: no tool calls are left in this round" in last_prompt(gateway)


def test_the_same_query_spelled_with_its_defaults_is_not_run_twice(engine: Any) -> None:
    tool = Counting("get_scanner_statistics")
    tool.spec = tool.spec.model_copy(
        update={
            "input_schema": {
                "type": "object",
                "properties": {"level": {"enum": ["scanner", "hub"], "default": "scanner"}},
            }
        }
    )
    gateway = FakeModelGateway(
        script=[
            call("get_scanner_statistics"),
            call("get_scanner_statistics", level="scanner"),
            call("get_scanner_statistics", level="hub"),
            decide(),
        ]
    )
    run(engine, "scanner_fault", reasoner=investigator(gateway, [], [tool]))
    assert tool.calls == 2  # {} and {"level": "scanner"} are one query; "hub" is another
    assert "same query as T1.1: cite T1.1" in last_prompt(gateway)


def test_a_rejected_decision_gets_one_more_turn(engine: Any) -> None:
    wrong = valid_answer()
    wrong["findings"] = [{"kind": "verified_fact", "statement": "x", "evidence_refs": ["T9"]}]
    gateway = FakeModelGateway(
        script=[{"step": "decide", "arguments": {}, "draft": wrong}, decide()]
    )
    made: list[InvestigatorReasoner] = []
    case, *_ = run(
        engine, "scanner_fault", reasoner=investigator(gateway, made, [Counting("lookup_policy")])
    )
    assert case.state is CaseState.AWAITING_APPROVAL
    assert "rejected: a finding cites an id that is not in the briefing" in last_prompt(gateway)


def test_a_rejected_decision_is_logged_with_its_reason_and_explained_with_code_s_ids(
    engine: Any,
) -> None:
    """ADR 0073: the exact reason is on the record; the model is told what it may cite, from
    code's own ids, never its own words quoted back."""
    recorded: list[Turn] = []
    wrong = valid_answer()
    wrong["findings"] = [{"kind": "verified_fact", "statement": "x", "evidence_refs": ["T9"]}]
    gateway = FakeModelGateway(
        script=[
            call("lookup_policy"),
            {"step": "decide", "arguments": {}, "draft": wrong},
            decide(),
        ]
    )
    run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, [], [Counting("lookup_policy")], record=recorded.append),
    )
    decisions = [t.result for t in recorded if t.step == "decide"]
    assert decisions == [
        "rejected: a finding cites an id that is not in the briefing; not citable: T9",
        "accepted",
    ]
    told = last_prompt(gateway)
    assert "citable tool results: T1.1, tool:lookup_policy" in told
    assert "not citable: T9" not in told  # the model's own id is never quoted back


def test_an_answer_that_fails_the_schema_twice_is_logged_with_why(engine: Any) -> None:
    recorded: list[Turn] = []
    gateway = FakeModelGateway(script=["not json", "still not json"])
    case, *_ = run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, [], [Counting("lookup_policy")], record=recorded.append),
    )
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert [t.step for t in recorded] == ["invalid_output"] and recorded[0].result


def test_the_prompt_shows_argument_bounds_and_the_case_s_accepted_ids(engine: Any) -> None:
    class Hinted(Counting):
        def accepted_values(self, ctx: ToolContext) -> str:
            return "packaging_id: any, PKG-A (small box)"

    tool = Hinted("search_past_shipments")
    tool.spec = tool.spec.model_copy(
        update={
            "input_schema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
            }
        }
    )
    gateway = FakeModelGateway(script=[decide()])
    run(engine, "scanner_fault", reasoner=investigator(gateway, [], [tool]))
    shown = last_prompt(gateway)
    assert "limit (integer 1-10)" in shown
    assert "Accepted ids for this case: packaging_id: any, PKG-A (small box)" in shown


def test_every_turn_is_recorded_with_its_cost(engine: Any) -> None:
    recorded: list[Turn] = []
    gateway = FakeModelGateway(script=[call("lookup_policy"), decide()])
    made: list[InvestigatorReasoner] = []
    run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, made, [Counting("lookup_policy")], record=recorded.append),
    )
    assert [t.step for t in recorded] == ["lookup_policy", "decide"]
    assert all(t.input_tokens > 0 and t.output_tokens > 0 for t in recorded)
    assert recorded[0].tool_ok is True and recorded[0].arguments == "{}"


def test_the_real_tools_on_a_generated_dispute(engine: Any) -> None:
    """IC-09's packaging change: the briefing labels the comparison, the real past-shipments tool
    finds the new box, and the agent can uphold citing it - with no hub request."""
    from dispute_agent.modules.evidence.synthetic_shipments.tools import (
        PastShipmentsTool,
        ScannerStatisticsTool,
    )

    upheld = valid_answer(action="uphold_charge", action_parameters={})
    upheld["findings"] = [
        {
            "kind": "verified_fact",
            "statement": "Shipments in the new box match.",
            "evidence_refs": ["T1.1"],
        }
    ]
    gateway = FakeModelGateway(
        script=[
            call("search_past_shipments", packaging_id="PKG-PS-BOX-MAT", date_from="2026-09-01"),
            {"step": "decide", "arguments": {}, "draft": upheld},
        ]
    )
    made: list[InvestigatorReasoner] = []
    case, *_ = run(
        engine,
        "inv_ps_mat_packaging_change",
        reasoner=investigator(gateway, made, [PastShipmentsTool(), ScannerStatisticsTool()]),
    )
    first, second = (str(r.prompt.messages[-1].content) for r in gateway.requests)
    assert "authoritative comparison with the seller's past shipments" in first
    assert "this parcel's scan and tracking history" in first or "shipment status" in first
    assert '"basis": "supplementary"' in second and '"sample_size"' in second
    assert case.state is CaseState.AWAITING_APPROVAL  # the uphold email waits for a person


# -- post-audit corrections (2026-11-27) --------------------------------------------------------


def test_an_outage_before_the_first_answer_falls_back_like_every_engine(engine: Any) -> None:
    """AUD-10: no model turn yet, so the station's rules baseline decides. AUD-09: the failed
    call is on the record."""
    recorded: list[Turn] = []
    gateway = FakeModelGateway(script=["unavailable"] * 4)
    case, *_ = run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, [], [Counting("lookup_policy")], record=recorded.append),
        fallback=True,
    )
    assert case.state is CaseState.AWAITING_APPROVAL
    assert [t.step for t in recorded] == ["model_error"]


def test_an_outage_mid_investigation_goes_to_a_person(engine: Any) -> None:
    recorded: list[Turn] = []
    gateway = FakeModelGateway(script=[call("lookup_policy"), *["unavailable"] * 4])
    case, *_ = run(
        engine,
        "scanner_fault",
        reasoner=investigator(gateway, [], [Counting("lookup_policy")], record=recorded.append),
        fallback=True,
    )
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert case.review_reason is not None
    assert case.review_reason.code is ReviewCode.INVALID_MODEL_OUTPUT
    assert [t.step for t in recorded] == ["lookup_policy", "model_error"]


def test_a_tool_this_run_does_not_offer_spends_a_step_and_cannot_be_cited(engine: Any) -> None:
    """AUD-09: a missing tool is a note, not a crash. AUD-22: only an answer can be cited."""
    citing = valid_answer()
    citing["findings"] = [{"kind": "verified_fact", "statement": "x", "evidence_refs": ["T1.1"]}]
    gateway = FakeModelGateway(
        script=[
            call("search_past_disputes"),
            {"step": "decide", "arguments": {}, "draft": citing},
            decide(),
        ]
    )
    made: list[InvestigatorReasoner] = []
    case, *_ = run(
        engine, "scanner_fault", reasoner=investigator(gateway, made, [Counting("lookup_policy")])
    )
    assert case.state is CaseState.AWAITING_APPROVAL
    second = str(gateway.requests[1].prompt.messages[-1].content)
    assert "not available in this run" in second
    assert "rejected: a finding cites an id that is not in the briefing" in last_prompt(gateway)


def test_the_briefing_is_marked_for_the_cache_only_where_several_turns_can_follow(
    engine: Any, tmp_path: Path
) -> None:
    """AUD-16: the agentic arm's turns share the briefing; a one-call arm would pay the
    cache write's premium and never read it back."""
    from dispute_agent.infra.sqlite.engine import (
        apply_schema,
        create_sqlite_engine,
        schema_sql_path,
    )
    from dispute_agent.kernel.domain.reasoning import PROMPT_CACHE_BREAK

    agentic = FakeModelGateway(script=[decide()])
    run(engine, "scanner_fault", reasoner=investigator(agentic, [], [Counting("lookup_policy")]))
    assert PROMPT_CACHE_BREAK in last_prompt(agentic)

    other = create_sqlite_engine(tmp_path / "other.sqlite3")
    apply_schema(other, schema_sql_path().read_text(encoding="utf-8"))
    single = FakeModelGateway(script=[decide()])
    run(other, "scanner_fault", reasoner=investigator(single, [], [], agentic=False))
    assert PROMPT_CACHE_BREAK not in last_prompt(single)
