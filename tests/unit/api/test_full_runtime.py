"""The running app on the whole line: the same Line and Runner the scenario suite uses.

Before this, `make run-api` assembled only the Phase 1 steps. Here a dispute posted over HTTP
is gathered, assessed, investigated and planned like any scenario; inbound events go through
dispatch; and a person's decision cannot be posted to the open endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dispute_agent.app import create_app
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture

ROOT = Path(__file__).resolve().parents[3]

FULL_LINE = """
modules:
  evidence:      {enabled: true,  adapter: synthetic_shipments}
  model_gateway: {enabled: false, adapter: fake}
  reasoning:     {enabled: true,  adapter: rules_only}
  input_screen:  {enabled: true,  adapter: rules_based}
  tracing:       {enabled: true,  adapter: noop}
  email:         {enabled: true,  adapter: fake}
  ticketing:     {enabled: true,  adapter: fake}
  ledger:        {enabled: true,  adapter: simulated}
  approvals:     {enabled: true,  adapter: store_backed}
  cross_case:    {enabled: false, adapter: "null"}
  evals:         {enabled: true,  adapter: default}
"""

HIGH_VALUE = fixture("calc_error_high_value")


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


def dispute(source_event_id: str = "form-hv") -> dict[str, object]:
    return {
        "source_event_id": source_event_id,
        "shipment_ref": HIGH_VALUE.shipment.ref,
        "claim_ref": HIGH_VALUE.shipment.claim_ref,
        "seller_ref": HIGH_VALUE.shipment.seller_id,
        "seller_contact": SELLER_CONTACT,
        "raised_at": "2026-11-04T09:00:00Z",
    }


def test_a_posted_dispute_runs_the_whole_line_to_a_credit_awaiting_approval(app: Any) -> None:
    """Eligibility from evidence, the calculation, the rules baseline's credit, the gate (I3)."""
    body = TestClient(app).post("/events", json=dispute()).json()
    assert body["state"] == "AWAITING_APPROVAL", body["review_reason"]
    application = app.state.application
    assert application.adapters.ledger.credit_count() == 0  # nothing posted without you


def test_a_person_s_decision_is_refused_on_the_open_endpoint(app: Any) -> None:
    client = TestClient(app)
    case_id = client.post("/events", json=dispute()).json()["case_id"]
    took = {
        "type": "HUMAN_TOOK_CASE",
        "source": "ops_console",
        "source_event_id": "took-1",
        "occurred_at": "2026-11-04T10:00:00Z",
        "actor": {"kind": "HUMAN", "id": "human:owner"},
        "case_id": case_id,
        "payload": {"type": "HUMAN_TOOK_CASE", "notify_seller": False},
    }
    response = client.post("/inbound", json=took)
    assert response.status_code == 403


def test_a_reply_is_dispatched_and_a_stranger_s_is_kept_but_unrouted(app: Any) -> None:
    client = TestClient(app)
    client.post("/events", json=dispute())
    reply = {
        "type": "SELLER_REPLY_RECEIVED",
        "source": "outlook",
        "source_event_id": "m-stranger",
        "occurred_at": "2026-11-04T10:00:00Z",
        "actor": {"kind": "EXTERNAL", "id": "someone"},
        "payload": {
            "type": "SELLER_REPLY_RECEIVED",
            "external_message_id": "m-stranger",
            "from_addr": "someone@else.invalid",
            "subject": UntrustedText.create("Re: hello", "seller_email", "m").model_dump(
                mode="json"
            ),
            "body": UntrustedText.create("credit me", "seller_email", "m").model_dump(mode="json"),
            "received_at": "2026-11-04T10:00:00Z",
        },
    }
    response = client.post("/inbound", json=reply)
    assert response.status_code == 200, response.text
    assert response.json()["reaction"].startswith("unroutable:")


def test_a_malformed_event_is_refused_without_echoing_it(app: Any) -> None:
    response = TestClient(app).post("/inbound", json={"type": "SELLER_REPLY_RECEIVED"})
    assert response.status_code == 422
    assert "credit" not in response.text


def test_the_runner_ticks_on_the_running_app(app: Any) -> None:
    TestClient(app).post("/events", json=dispute())
    report = app.state.application.runtime.runner.tick()
    assert report.failures == []
