"""The failure patterns of the two paid runs (2026-11-27), replayed with a scripted model (free).

Each test plays what the real model did - the same mistake, through the real station, loop,
gateway retry and checks - and asserts what now happens instead (ADR 0073).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from dispute_agent.packs.weight_dispute.baseline import comparable_support
from tests.e2e.test_phase3_investigate import run
from tests.model_fixtures import valid_answer
from tests.unit.modules.reasoning.test_investigator import (
    Counting,
    call,
    decide,
    investigator,
    last_prompt,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dispute_agent.modules.reasoning.llm.investigator import Turn

UPHOLD_REJECTED = "uphold_charge needs verified support"


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


def deciding(**changes: Any) -> dict[str, Any]:
    draft = valid_answer()
    draft.update(changes)
    return {"step": "decide", "arguments": {}, "draft": draft}


def cites(*refs: str) -> list[dict[str, Any]]:
    return [{"kind": "verified_fact", "statement": "s", "evidence_refs": list(refs)}]


def played(
    db: Any, script: list[Any], tools: list[Any] | None = None
) -> tuple[Any, Any, list[Turn]]:
    recorded: list[Turn] = []
    gateway = FakeModelGateway(script=script)
    case, *_ = run(
        db,
        "scanner_fault",
        reasoner=investigator(
            gateway, [], tools or [Counting("lookup_policy")], record=recorded.append
        ),
    )
    return case, gateway, recorded


# -- run 2: citing a seller note that does not exist ---------------------------------------------


def test_a_case_without_a_seller_note_says_so_and_a_citation_of_it_is_refused(engine: Any) -> None:
    case, gateway, recorded = played(
        engine, [deciding(findings=cites("dispute_statement")), decide()]
    )
    first = str(gateway.requests[0].prompt.messages[-1].content)
    assert "There is no dispute_statement: the seller wrote no note" in first
    assert "<citable_ids>" in first and "dispute_statement," not in first
    assert recorded[0].result.startswith(
        "rejected: a finding cites an id that is not in the briefing"
    )
    assert "not citable: dispute_statement" in recorded[0].result
    assert case.state is CaseState.AWAITING_APPROVAL  # the retry, told what may be cited, decided


# -- run 2: answers that break the schema ---------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "named"),
    [
        (
            {"action_parameters": {"reason_code": None, "note": "x"}},
            "field not allowed: note (allowed: reason_code, remeasurement_question, requested_items)",
        ),
        (
            {"findings": [{"kind": "observation", "statement": "s", "evidence_refs": []}]},
            "not an allowed value; use one of: verified_fact, plausible_explanation",
        ),
    ],
)
def test_a_schema_error_is_named_to_the_retry_and_logged(
    engine: Any,
    broken: dict[str, Any],
    named: str,
) -> None:
    case, gateway, _ = played(engine, [deciding(**broken), decide()])
    retry = str(gateway.requests[1].prompt.messages[-1].content)
    assert "previous answer was not accepted" in retry and named in retry
    assert case.state is CaseState.AWAITING_APPROVAL
    assert gateway.provider_calls() == 2  # the existing retry, not an extra call


def test_twice_invalid_is_logged_with_the_field_names(engine: Any) -> None:
    bad = deciding(action_parameters={"note": "x"})
    case, _, recorded = played(engine, [bad, bad])
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert recorded[-1].step == "invalid_output"
    assert "field not allowed: note" in recorded[-1].result


# -- run 1: made-up tool arguments (now answered with what to fix) -------------------------------


def test_a_made_up_limit_is_answered_with_the_rule(engine: Any) -> None:
    from dispute_agent.modules.evidence.synthetic_shipments.tools import PastShipmentsTool

    _, gateway, _ = played(
        engine,
        [call("search_past_shipments", limit=20), decide()],
        tools=[PastShipmentsTool()],
    )
    assert "limit: Input should be less than or equal to 10" in last_prompt(gateway)


# -- the five-record guardrail, in code -----------------------------------------------------------


def search_answer(sample: int, ratio: str = "1.00", packaging: str = "PKG-A") -> dict[str, Any]:
    return {
        "query": {"packaging_id": packaging, "trusted_only": True},
        "differs_from_authoritative": [f"packaging: {packaging}"],
        "stats": {"sample_size": sample, "this_parcel_volume_to_typical": ratio},
    }


def test_an_uphold_with_no_verified_basis_is_refused(engine: Any) -> None:
    case, _, recorded = played(
        engine,
        [
            deciding(action="uphold_charge", action_parameters={}),
            deciding(action="uphold_charge", action_parameters={}),
        ],
    )
    assert all(UPHOLD_REJECTED in t.result for t in recorded if t.step == "decide")
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW


@pytest.mark.parametrize(("sample", "allowed"), [(4, False), (5, True)])
def test_an_uphold_on_history_needs_five_trusted_comparable_shipments(
    engine: Any,
    sample: int,
    allowed: bool,
) -> None:
    search = Counting("search_past_shipments", answer=search_answer(sample))
    uphold = deciding(action="uphold_charge", action_parameters={}, findings=cites("T1.1"))
    case, _, recorded = played(
        engine,
        [call("search_past_shipments", packaging_id="PKG-A"), uphold, uphold],
        tools=[search],
    )
    refused = [t for t in recorded if t.step == "decide" and UPHOLD_REJECTED in t.result]
    assert (not refused) is allowed
    assert (case.state is CaseState.AWAITING_APPROVAL) is allowed


def test_scanner_statistics_or_past_disputes_alone_never_support_an_uphold(engine: Any) -> None:
    scanner = Counting("get_scanner_statistics", answer={"windows": {}})
    uphold = deciding(action="uphold_charge", action_parameters={}, findings=cites("T1.1"))
    case, _, recorded = played(
        engine, [call("get_scanner_statistics"), uphold, uphold], tools=[scanner]
    )
    assert any(UPHOLD_REJECTED in t.result for t in recorded if t.step == "decide")
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW


@pytest.mark.parametrize(
    ("answer", "support"),
    [
        (search_answer(5), 5),
        (search_answer(9, ratio="1.24"), 9),
        (search_answer(9, ratio="1.40"), 0),  # they do not measure like this parcel
        (search_answer(9, packaging="any"), 0),  # a slice of every packaging is not comparable
        ({**search_answer(9), "differs_from_authoritative": ["other product: X"]}, 0),
        ({**search_answer(9), "query": {"packaging_id": "PKG-A", "trusted_only": False}}, 0),
        ({}, 0),
    ],
)
def test_what_counts_as_trusted_comparable_shipments(answer: dict[str, Any], support: int) -> None:
    assert comparable_support(answer) == support


# -- pre-run-3 checks: seller evidence must support; history never overrides a contradiction ---


def briefing(
    reading: dict[str, Any] | None = None, *, hub: str | None = None, outstanding: bool = False
) -> Any:
    from types import SimpleNamespace

    from dispute_agent.kernel.domain.enums import EvidenceStatus

    def item(key: str, value: Any) -> Any:
        return SimpleNamespace(
            result=SimpleNamespace(requirement_key=key, status=EvidenceStatus.FOUND, value=value)
        )

    items = [
        item("shipment.measured_dims", {"length_cm": "30", "width_cm": "22", "height_cm": "15"})
    ]
    if reading is not None:
        items.append(item("seller_reply", reading))
    if hub is not None:
        items.append(item("ops.finding", {"kind": hub}))
    return SimpleNamespace(
        evidence=SimpleNamespace(items=items),
        contact=SimpleNamespace(
            reply_in_round=reading is not None, remeasurement_outstanding=outstanding
        ),
        assessment=SimpleNamespace(definite_errors=()),
    )


def reading(
    intent: str = "provides_requested_evidence", status: str = "provided", **more: Any
) -> dict[str, Any]:
    return {
        "intent": intent,
        "requested_items_status": [{"item": "a", "status": status}],
        "claims": [],
        "manipulation_flags": [],
    } | more


@pytest.mark.parametrize(
    ("given", "supports"),
    [
        (reading(), True),  # everything asked for, unflagged, no contrary size
        (reading(claims=[{"kind": "dimensions", "text": "30 x 22 x 15 cm"}]), True),
        (reading(claims=[{"kind": "dimensions", "text": "30 x 20 x 10 cm"}]), False),  # smaller
        (reading(claims=[{"kind": "dimensions", "text": "about a big box"}]), False),  # unreadable
        (reading(claims=[{"kind": "weight", "text": "400 g"}]), False),
        (reading(status="partial"), False),
        (reading(intent="disputes_again_no_new_info", status="missing"), False),
        (reading(intent="irrelevant", status="missing"), False),
        (reading(manipulation_flags=["instruction_like"]), False),
        (None, False),  # no reply: nothing to support anything
    ],
)
def test_a_seller_reply_counts_only_when_it_supports_the_charge(given: Any, supports: bool) -> None:
    from dispute_agent.packs.weight_dispute.baseline import seller_supports

    assert seller_supports(briefing(given)) is supports


def test_a_photo_merely_existing_is_not_a_basis() -> None:
    from types import SimpleNamespace

    from dispute_agent.kernel.domain.enums import EvidenceStatus
    from dispute_agent.packs.weight_dispute.baseline import uphold_basis

    b = briefing()
    b.evidence.items.append(
        SimpleNamespace(
            result=SimpleNamespace(
                requirement_key="shipment.image_description",
                status=EvidenceStatus.FOUND,
                value={"present": True},
            )
        )
    )
    assert uphold_basis(b) is None


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (briefing(hub="inspected"), ["the hub's check is not concluded"]),
        (briefing(outstanding=True), ["a remeasurement is outstanding"]),
        (
            briefing(reading(status="partial", intent="partial_evidence")),
            ["the seller's new evidence is not resolved"],
        ),
        (briefing(reading()), []),
        (briefing(), []),
    ],
)
def test_history_never_overrides_a_contradiction(given: Any, expected: list[str]) -> None:
    from dispute_agent.packs.weight_dispute.baseline import history_contradictions

    assert history_contradictions(given) == expected


@pytest.mark.parametrize(
    ("tool", "data", "found"),
    [
        (
            "get_scanner_statistics",
            {
                "level": "scanner",
                "windows": {
                    "last_60_days": {
                        "this_scanner": {"share_over_1_25x_declared": "0.40"},
                        "other_scanners_at_hub": {"share_over_1_25x_declared": "0.00"},
                    }
                },
            },
            True,
        ),
        (
            "get_scanner_statistics",
            {
                "level": "scanner",
                "windows": {
                    "last_60_days": {
                        "this_scanner": {"share_over_1_25x_declared": "0.05"},
                        "other_scanners_at_hub": {"share_over_1_25x_declared": "0.00"},
                    }
                },
            },
            False,
        ),
        (
            "get_scanner_statistics",
            {
                "level": "hub",
                "windows": {
                    "last_60_days": {
                        "this_hub": {"share_over_1_25x_declared": "0.30"},
                        "other_hubs": {"share_over_1_25x_declared": "0.02"},
                    }
                },
            },
            True,
        ),
        (
            "get_scanner_statistics",
            {
                "windows": {
                    "last_60_days": {
                        "this_scanner": "insufficient_data",
                        "other_scanners_at_hub": {},
                    }
                }
            },
            False,
        ),
        ("analyze_parcel_image", {"size_from_photo": "looks_smaller_than_measured"}, True),
        ("analyze_parcel_image", {"parcel_count": "more_than_one"}, True),
        (
            "analyze_parcel_image",
            {"size_from_photo": "looks_consistent_with_measured", "parcel_count": "one"},
            False,
        ),
    ],
)
def test_what_a_tool_answer_contradicts(tool: str, data: dict[str, Any], found: bool) -> None:
    from dispute_agent.packs.weight_dispute.baseline import tool_contradictions

    assert bool(tool_contradictions(tool, data)) is found


def test_five_comparable_records_do_not_outweigh_a_drifting_scanner(engine: Any) -> None:
    search = Counting("search_past_shipments", answer=search_answer(9))
    scanner = Counting(
        "get_scanner_statistics",
        answer={
            "level": "scanner",
            "windows": {
                "last_60_days": {
                    "this_scanner": {"share_over_1_25x_declared": "0.40"},
                    "other_scanners_at_hub": {"share_over_1_25x_declared": "0.00"},
                }
            },
        },
    )
    uphold = deciding(action="uphold_charge", action_parameters={}, findings=cites("T1.1"))
    case, _, recorded = played(
        engine,
        [
            call("search_past_shipments", packaging_id="PKG-A"),
            call("get_scanner_statistics"),
            uphold,
            uphold,
        ],
        tools=[search, scanner],
    )
    refused = [t.result for t in recorded if t.step == "decide"]
    assert refused and all(
        "contradicted: this scanner reads high against its peers" in r for r in refused
    )
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
