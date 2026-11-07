"""The closed list of requested evidence (ADR 0043, case_investigator v2).

The model chooses items; it never words them. These tests hold the list's three promises:
the prompt and the pack name the same items, no item carries a number a seller would read,
and an item off the list never reaches a seller - it sends the case to a person.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.enums import CaseState, ReviewCode
from dispute_agent.kernel.policy.numbers import TOKEN, number_word_in
from dispute_agent.modules.evals.scenarios.answers import REQUESTED_ITEMS
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from dispute_agent.modules.reasoning.llm.reasoner import VERSION, LlmReasoner, allowed_items
from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry
from dispute_agent.packs.weight_dispute.baseline import B7_ITEMS
from dispute_agent.packs.weight_dispute.requested_items import EVIDENCE_ITEMS
from tests.e2e.test_phase3_investigate import run
from tests.model_fixtures import PROMPTS, valid_answer

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

V1, V2 = PROMPTS / "case_investigator" / "v1", PROMPTS / "case_investigator" / "v2"


def _schema(folder: Path) -> dict[str, Any]:
    return dict(json.loads((folder / "schema.json").read_text(encoding="utf-8")))


def _template(folder: Path) -> str:
    return (folder / "user.md.j2").read_text(encoding="utf-8")


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


def _reasoner(gateway: FakeModelGateway) -> Callable[[Any, Any], LlmReasoner]:
    def factory(ids: Any, clock: Any) -> LlmReasoner:
        return LlmReasoner(
            prompts=FilePromptRegistry(PROMPTS, allow_inactive=True),
            gateway=gateway,
            ids=ids,
            clock=clock,
        )

    return factory


def _asks_for(*items: str) -> dict[str, Any]:
    return valid_answer(
        action="request_seller_evidence", action_parameters={"requested_items": list(items)}
    )


class TestTheList:
    def test_the_engine_renders_v2(self) -> None:
        assert VERSION == "v2"

    def test_the_prompt_and_the_pack_name_the_same_items_in_order(self) -> None:
        assert allowed_items(_schema(V2)) == frozenset(EVIDENCE_ITEMS)
        listed = _schema(V2)["properties"]["action_parameters"]["properties"]
        assert tuple(listed["requested_items"]["items"]["enum"]) == EVIDENCE_ITEMS

    @pytest.mark.parametrize("item", EVIDENCE_ITEMS)
    def test_no_item_carries_a_number_a_seller_would_read(self, item: str) -> None:
        assert number_word_in(item) is None and TOKEN.search(item) is None

    def test_the_system_prompt_lists_every_item_verbatim(self) -> None:
        system = (V2 / "system.md").read_text(encoding="utf-8")
        assert all(f"  - {item}\n" in system for item in EVIDENCE_ITEMS)

    def test_the_rules_and_the_recorded_answers_only_use_listed_items(self) -> None:
        assert set(B7_ITEMS) <= set(EVIDENCE_ITEMS)
        assert set(REQUESTED_ITEMS) <= set(EVIDENCE_ITEMS)
        fixture_items = valid_answer()["action_parameters"]["requested_items"]
        assert set(fixture_items) <= set(EVIDENCE_ITEMS)

    def test_v2_changes_nothing_else(self) -> None:
        """A released prompt is never edited; v2 is v1 plus the closed list."""
        old, new = _schema(V1), _schema(V2)
        for schema in (old, new):
            del schema["properties"]["action_parameters"]["properties"]["requested_items"]
        assert old == new
        assert _template(V1) == _template(V2)
        assert allowed_items(_schema(V1)) is None  # v1 stays open, as released


class TestOnACase:
    def test_listed_items_reach_the_reviewer(self, engine: Any) -> None:
        gateway = FakeModelGateway(script=[_asks_for(EVIDENCE_ITEMS[0], EVIDENCE_ITEMS[2])])
        case, approvals, *_ = run(engine, "scanner_fault", reasoner=_reasoner(gateway))
        assert case.state is CaseState.AWAITING_APPROVAL and len(approvals) == 1
        assert gateway.requests[0].prompt.version == "v2"

    def test_an_item_the_model_worded_itself_goes_to_a_person(self, engine: Any) -> None:
        worded = _asks_for("Two photos of the packed box")
        gateway = FakeModelGateway(script=[worded, worded])
        case, approvals, *_ = run(engine, "scanner_fault", reasoner=_reasoner(gateway))
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW and not approvals
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.INVALID_MODEL_OUTPUT

    def test_one_bad_answer_then_a_listed_one_is_retried_not_escalated(self, engine: Any) -> None:
        gateway = FakeModelGateway(
            script=[_asks_for("Two photos of the packed box"), _asks_for(EVIDENCE_ITEMS[1])]
        )
        case, *_ = run(engine, "scanner_fault", reasoner=_reasoner(gateway))
        assert case.state is CaseState.AWAITING_APPROVAL
