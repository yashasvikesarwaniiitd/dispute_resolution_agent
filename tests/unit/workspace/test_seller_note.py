"""ADR 0070: a seller's note on raising a dispute is stored on the case and reaches the agent.

The note is the case's dispute statement: untrusted text (I5) that every round's briefing
carries, and that never sets a figure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.modules.reasoning.llm.briefing_view import STATEMENT_ID, build_view
from dispute_agent.ui.seller import NOTE_CHARS
from tests.unit.packs.test_baseline_matches_scenarios import briefing_for
from tests.unit.workspace.test_workspace_api import SELLER, WRONG_SLAB, client

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from dispute_agent.kernel.domain.case import CaseFacts

__all__ = ["client"]  # the fixture, shared with the workspace API tests

NOTE = "Packed in a 21 x 13 x 4 cm padded mailer; the hub must have measured the outer carton."


def raise_with(client: TestClient, note: str) -> Any:
    return client.post(
        "/api/ws/sellers/raise",
        json={"seller_id": SELLER, "shipment_ref": WRONG_SLAB, "note": note},
    )


def stored_facts(client: TestClient, case_id: str) -> CaseFacts:
    engine = client.app.state.application.runtime.runner.engine  # type: ignore[attr-defined]
    with SqliteUnitOfWork(engine, clock=SystemClock()) as work:
        return work.cases.get(CaseId(case_id)).facts


def test_the_note_is_stored_shown_and_read_by_the_agent(client: TestClient) -> None:
    answer = raise_with(client, f"  {NOTE}\n").json()
    assert answer["ok"], answer
    case_id = answer["case_id"]

    statement = stored_facts(client, case_id).statement
    assert statement is not None
    assert (statement.text, statement.origin) == (NOTE, "dispute_form")

    assert client.get(f"/api/ws/cases/{case_id}").json()["statement"] == NOTE

    # Every round's briefing carries the case's facts, so the note is outside text the agent
    # reads, fenced and citable, in this round and every later one.
    briefing = briefing_for("calc_error_wrong_slab").model_copy(
        update={"facts": stored_facts(client, case_id)}
    )
    view = build_view(briefing)
    assert STATEMENT_ID in view.citable_ids
    assert NOTE in str(view.untrusted)


def test_no_note_means_no_statement(client: TestClient) -> None:
    case_id = raise_with(client, "   ").json()["case_id"]
    assert stored_facts(client, case_id).statement is None


def test_a_note_over_the_limit_is_refused_and_nothing_is_raised(client: TestClient) -> None:
    assert raise_with(client, "x" * (NOTE_CHARS + 1)).status_code == 422
    assert client.get("/api/ws/work").json()["items"] == []


def test_the_desk_and_the_api_share_one_limit() -> None:
    from dispute_agent.api.events import NOTE_CHARS as API_NOTE_CHARS

    assert API_NOTE_CHARS == NOTE_CHARS
