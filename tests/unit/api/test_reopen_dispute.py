"""A seller raises SHP-000001 again after it closed upheld (live demo, 2026-11-22).

The console posts a second dispute to `POST /events`. Intake hands the recently closed case
back for its reaction table, which reopens it (T42). The runtime used to treat that like a
dispute on an open case: it recorded the dispute, ran the closed state's own step (there is
none) and marked it processed. Two "Dispute raised" lines stacked on the trail and the case
never moved.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dispute_agent.app import create_app
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ActorKind, ApprovalDecision, CaseState
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture
from dispute_agent.ui.desk import CaseDesk
from tests.unit.api.test_full_runtime import FULL_LINE

ROOT = Path(__file__).resolve().parents[3]
KURTA = fixture("kurta_box_larger")  # SHP-000001: a photo case, so it goes to a person (B5)
OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    (tmp_path / "modules.yaml").write_text(FULL_LINE, encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()
    monkeypatch.setattr(
        "dispute_agent.app.Settings",
        lambda: Settings(app_env="test", test_seller_email=SELLER_CONTACT),
    )
    return create_app(config_dir=tmp_path, app_env="test", database=database)


def _raise(client: TestClient, source_event_id: str) -> dict[str, Any]:
    body = {
        "source_event_id": source_event_id,
        "shipment_ref": KURTA.shipment.ref,
        "claim_ref": KURTA.shipment.claim_ref,
        "seller_ref": KURTA.shipment.seller_id,
        "seller_contact": SELLER_CONTACT,
        "raised_at": datetime.now(UTC).isoformat(),
    }
    response = client.post("/events", json=body)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _work(app: Any) -> SqliteUnitOfWork:
    return SqliteUnitOfWork(app.state.application.runtime.runner.engine, clock=SystemClock())


def _state(app: Any, case_id: str) -> CaseState:
    with _work(app) as work:
        return work.cases.get(CaseId(case_id)).state


def _upheld(app: Any, client: TestClient) -> str:
    """Raise SHP-000001, uphold it as the owner, approve the email, and let it close."""
    first = _raise(client, "console-first")
    case_id = str(first["case_id"])
    assert first["state"] == "NEEDS_HUMAN_REVIEW"
    runner = app.state.application.runtime.runner
    with _work(app) as work:
        CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=OWNER).decide(
            work.cases.get(CaseId(case_id)), "uphold_charge", "The photo matches the hub."
        )
    runner.tick()
    assert _state(app, case_id) is CaseState.AWAITING_APPROVAL
    with _work(app) as work:
        for pending in work.approvals.pending(CaseId(case_id)):
            work.approvals.decide(
                pending.id, ApprovalDecision.APPROVED, OWNER, datetime.now(UTC), "Send it."
            )
    # The fake mailbox shows a sent email in Sent only after SEND_VISIBILITY_DELAY (2 s, real
    # time on the running app), as Gmail does after a moment: tick until it reads back.
    for _ in range(20):
        runner.tick()
        if _state(app, case_id) is CaseState.RESOLVED_UPHELD:
            break
        time.sleep(0.25)
    assert _state(app, case_id) is CaseState.RESOLVED_UPHELD
    return case_id


def test_raising_again_soon_after_an_upheld_close_reopens_the_same_case(app: Any) -> None:
    client = TestClient(app)
    case_id = _upheld(app, client)

    again = _raise(client, "console-second")

    assert again["case_id"] == case_id  # the same ticket, not a new one
    assert again["state"] != "RESOLVED_UPHELD"  # it moved: a fresh round ran
    with _work(app) as work:
        case = work.cases.get(CaseId(case_id))
        moved = [
            getattr(e.payload, "transition_id", None) for e in work.events.list_for_case(case.id)
        ]
    assert "T42" in moved  # reopened by the engine's own transition
    assert case.reopen_count == 1
    # The photo still needs a person (B5), so the fresh round ends with one again.
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
