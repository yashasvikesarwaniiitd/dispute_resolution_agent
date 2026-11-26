"""The Dispute Desk web app's API (/api/ws): a real dispute through the whole offline line -
raised from the seller view, listed in the reviewer's work, opened, approved - and every
refusal a person can meet on the way."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from web_app import create

from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from tests.unit.api.test_full_runtime import FULL_LINE

ROOT = Path(__file__).resolve().parents[3]
ADDRESS = "qa+threadlane@example.invalid"  # synthetic: email is the fake adapter here
SELLER = "SELL-0103"  # ThreadLane Apparel
WRONG_SLAB = "SHP-103026"  # billed 3 slabs for a 2-slab parcel: a ₹38.00 calculation error
TOKEN = "test-review-token"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.chdir(tmp_path)  # the console's stores live in ./data, as when it runs
    for name in ("CONSOLE_PASSWORD", "REVIEW_CONSOLE_TOKEN", "KILL_SWITCH"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TEST_SELLER_EMAIL", ADDRESS)
    (tmp_path / "data").mkdir()
    (tmp_path / "modules.yaml").write_text(FULL_LINE, encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "data" / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()
    monkeypatch.setattr(
        "dispute_agent.app.Settings",
        lambda: Settings(_env_file=None, app_env="test", test_seller_email=ADDRESS),
    )
    monkeypatch.setattr("dispute_agent.ui.web.router.Settings", lambda: Settings(_env_file=None))
    return TestClient(create(config_dir=tmp_path, app_env="test", database=database))


def raised(client: TestClient) -> str:
    answer = client.post(
        "/api/ws/sellers/raise", json={"seller_id": SELLER, "shipment_ref": WRONG_SLAB}
    ).json()
    assert answer["ok"], answer
    return str(answer["case_id"])


def credit(client: TestClient, case_id: str) -> dict[str, Any]:
    detail = client.get(f"/api/ws/cases/{case_id}").json()
    assert detail["kind"] == "credit"
    return dict(detail["approvals"][0])


def test_a_raised_dispute_reaches_proven_credits_with_code_s_equation(client: TestClient) -> None:
    case_id = raised(client)
    work = client.get("/api/ws/work").json()
    item = next(i for i in work["items"] if i["case_id"] == case_id)
    assert {"needs", "credits"} <= set(item["views"])
    assert item["tag"] == "Approve credit"
    counts = {v["key"]: v["count"] for v in work["views"]}
    assert counts["credits"] == 1 and counts["needs"] == 1

    approval = credit(client, case_id)
    assert approval["amount"] == "₹38.00"
    assert [t["label"] for t in approval["equation"]] == [
        "Billed",
        "Correct charge",
        "Already credited",
    ]


def test_approving_needs_the_review_token_and_a_listed_approver(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = credit(client, raised(client))
    url = f"/api/ws/approvals/{approval['approval_id']}"
    body = {"reviewer": "owner", "decision": "approve"}

    # No token configured: the queue that authorises real sends does not run (as Streamlit).
    assert client.post(url, json=body).status_code == 403

    monkeypatch.setattr(
        "dispute_agent.ui.web.router.Settings",
        lambda: Settings(_env_file=None, review_console_token=TOKEN),
    )
    assert client.post(url, json=body, headers={"X-Review-Token": "wrong"}).status_code == 401
    stranger = {"reviewer": "mallory", "decision": "approve"}
    assert client.post(url, json=stranger, headers={"X-Review-Token": TOKEN}).status_code == 403

    ok = client.post(url, json=body, headers={"X-Review-Token": TOKEN})
    assert ok.status_code == 200 and ok.json()["decision"] == "APPROVED"

    again = client.post(url, json=body, headers={"X-Review-Token": TOKEN})
    assert again.status_code == 409
    assert "nothing was done twice" in again.json()["detail"]


def test_a_rejection_needs_a_reason(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    approval = credit(client, raised(client))
    monkeypatch.setattr(
        "dispute_agent.ui.web.router.Settings",
        lambda: Settings(_env_file=None, review_console_token=TOKEN),
    )
    refused = client.post(
        f"/api/ws/approvals/{approval['approval_id']}",
        json={"reviewer": "owner", "decision": "reject", "note": "  "},
        headers={"X-Review-Token": TOKEN},
    )
    assert refused.status_code == 422


def test_the_kill_switch_pauses_approvals(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval = credit(client, raised(client))
    monkeypatch.setattr(
        "dispute_agent.ui.web.router.Settings",
        lambda: Settings(_env_file=None, review_console_token=TOKEN, kill_switch=True),
    )
    paused = client.post(
        f"/api/ws/approvals/{approval['approval_id']}",
        json={"reviewer": "owner", "decision": "approve"},
        headers={"X-Review-Token": TOKEN},
    )
    assert paused.status_code == 409


def test_a_site_password_closes_every_call_without_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "dispute_agent.ui.web.router.Settings",
        lambda: Settings(_env_file=None, console_password="open sesame"),
    )
    assert client.get("/api/ws/work").status_code == 401
    assert client.get("/api/ws/session", headers={"X-Console-Password": "nope"}).status_code == 401
    seen = client.get("/api/ws/session", headers={"X-Console-Password": "open sesame"})
    assert seen.status_code == 200 and seen.json()["needs_token"] is False


def test_a_decision_on_a_case_not_with_a_person_is_refused(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_id = raised(client)  # awaiting approval, not with a person
    monkeypatch.setattr(
        "dispute_agent.ui.web.router.Settings",
        lambda: Settings(_env_file=None, review_console_token=TOKEN),
    )
    refused = client.post(
        f"/api/ws/cases/{case_id}/decide",
        json={"reviewer": "owner", "choice": "take_over", "note": "mine"},
        headers={"X-Review-Token": TOKEN},
    )
    assert refused.status_code == 422


def test_the_seller_view_lists_the_ticket_with_what_is_happening(client: TestClient) -> None:
    case_id = raised(client)
    seller = client.get(f"/api/ws/sellers/{SELLER}").json()
    assert seller["can_raise"] is True
    assert any(s["ref"] == WRONG_SLAB and s["billing_error"] for s in seller["shipments"])
    mine = next(t for t in seller["tickets"] if t["case_id"] == case_id)
    assert mine["is_open"] and mine["headline"]
