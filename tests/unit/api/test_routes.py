"""Card P1-11: the two routes, through a real app and a real database.

The dedupe test is the one that matters. A dispute form can be double-submitted and a poller
can deliver twice; posting the same event twice must yield one case, not two, and the second
response must be the same answer rather than an error.

The `GET` test is the other half of why this project exists: a case's events, in order, each
naming the transition that moved it and the cause that triggered it. A state alone says where
a dispute got to; the diary says how and why.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dispute_agent.app import create_app
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path

ROOT = Path(__file__).resolve().parents[3]
SELLER = "seller@example.invalid"

PHASE_0_MODULES = """
modules:
  evidence:      {enabled: false, adapter: synthetic_shipments}
  model_gateway: {enabled: false, adapter: fake}
  reasoning:     {enabled: false, adapter: rules_only}
  input_screen:  {enabled: true,  adapter: rules_based}
  tracing:       {enabled: true,  adapter: noop}
  email:         {enabled: false, adapter: fake}
  ticketing:     {enabled: false, adapter: fake}
  ledger:        {enabled: false, adapter: simulated}
  approvals:     {enabled: false, adapter: store_backed}
  cross_case:    {enabled: false, adapter: "null"}
  evals:         {enabled: true,  adapter: default}
"""


def a_dispute(source_event_id: str = "form-1", **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "source_event_id": source_event_id,
        "shipment_ref": "SHP-0001",
        "claim_ref": "CHG-0001",
        "seller_ref": "SELLER-0001",
        "seller_contact": SELLER,
        "raised_at": "2026-03-01T09:30:00Z",
    }
    body.update(overrides)
    return body


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    (tmp_path / "modules.yaml").write_text(PHASE_0_MODULES, encoding="utf-8")
    # The shipped policy file, so the test app is configured exactly as a real one is.
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()

    monkeypatch.setattr(
        "dispute_agent.app.Settings",
        lambda: Settings(app_env="test", test_seller_email=SELLER),
    )
    return TestClient(create_app(config_dir=tmp_path, app_env="test", database=database))


class TestPostEvents:
    def test_a_dispute_opens_a_case(self, client: TestClient) -> None:
        response = client.post("/events", json=a_dispute())
        assert response.status_code == 200
        body = response.json()
        assert body["duplicate"] is False
        assert body["case_id"].startswith("case_")

    def test_the_case_reaches_a_person_in_phase_1(self, client: TestClient) -> None:
        """No pack and no reasoner yet, so the honest outcome is a human with a reason."""
        body = client.post("/events", json=a_dispute()).json()
        assert body["state"] == "NEEDS_HUMAN_REVIEW"
        assert body["review_reason"]

    def test_the_same_event_twice_yields_one_case(self, client: TestClient) -> None:
        """Scenario S11. A double-submitted form must not open two disputes."""
        first = client.post("/events", json=a_dispute()).json()
        second = client.post("/events", json=a_dispute()).json()

        assert second["duplicate"] is True
        assert second["case_id"] == first["case_id"]
        assert second["inbox_id"] == first["inbox_id"]

    def test_a_different_event_opens_its_own_case(self, client: TestClient) -> None:
        first = client.post("/events", json=a_dispute("form-1")).json()
        second = client.post("/events", json=a_dispute("form-2", claim_ref="CHG-0002")).json()
        assert second["case_id"] != first["case_id"]

    def test_an_unlisted_seller_is_refused_with_422(self, client: TestClient) -> None:
        """I11, scenario S27."""
        response = client.post("/events", json=a_dispute(seller_contact="stranger@example.com"))
        assert response.status_code == 422
        assert "allowlist" in response.json()["detail"]

    def test_nothing_is_stored_when_it_is_refused(self, client: TestClient) -> None:
        client.post("/events", json=a_dispute(seller_contact="stranger@example.com"))
        listed = client.get("/cases/case_00000000000000000000000001")
        assert listed.status_code == 404

    def test_an_unknown_field_is_refused(self, client: TestClient) -> None:
        """extra='forbid': a typo in a client is a failure, not a silently ignored field."""
        response = client.post("/events", json=a_dispute(sellerContact=SELLER))
        assert response.status_code == 422


class TestGetCase:
    def test_it_returns_the_state_and_the_events(self, client: TestClient) -> None:
        case_id = client.post("/events", json=a_dispute()).json()["case_id"]
        body = client.get(f"/cases/{case_id}").json()
        assert body["id"] == case_id
        assert body["state"] == "NEEDS_HUMAN_REVIEW"
        assert body["events"]

    def test_the_events_are_in_order(self, client: TestClient) -> None:
        case_id = client.post("/events", json=a_dispute()).json()["case_id"]
        events = client.get(f"/cases/{case_id}").json()["events"]
        assert [e["seq"] for e in events] == list(range(1, len(events) + 1))

    def test_each_event_names_its_cause(self, client: TestClient) -> None:
        case_id = client.post("/events", json=a_dispute()).json()["case_id"]
        events = client.get(f"/cases/{case_id}").json()["events"]
        assert all(e["cause_kind"] and e["cause_ref"] for e in events)

    def test_a_state_change_names_its_transition(self, client: TestClient) -> None:
        """What makes a disputed decision reviewable months later."""
        case_id = client.post("/events", json=a_dispute()).json()["case_id"]
        events = client.get(f"/cases/{case_id}").json()["events"]
        changes = [e for e in events if e["type"] == "STATE_CHANGED"]
        assert changes
        assert all(e["transition_id"] and e["to_state"] for e in changes)

    def test_an_unknown_case_is_404(self, client: TestClient) -> None:
        assert client.get("/cases/case_nope").status_code == 404

    def test_the_review_reason_is_rendered(self, client: TestClient) -> None:
        case_id = client.post("/events", json=a_dispute()).json()["case_id"]
        body = client.get(f"/cases/{case_id}").json()
        assert body["review_reason"]


class TestHealthzStillWorks:
    def test_it_lists_every_module(self, client: TestClient) -> None:
        body = client.get("/healthz").json()
        assert len(body["modules"]) == 11
