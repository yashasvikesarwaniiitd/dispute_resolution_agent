"""Disputes through the running app, opening a REAL Jira issue (owner's test project).

Opt-in (`@pytest.mark.integration`, `make test-integration`): it needs the Jira settings in
`.env` (docs/SETUP_CHANNELS.md). Email stays on the fake mailbox.

With the rules-only reasoner (the shipped setting) the app opens a Jira issue in one place: the
cross-case investigation (P7-05). Five disputes through scanner SCN-03-2 are posted over HTTP;
the runner's cross-case job finds the pattern and proposes the parent investigation; the
kernel's policy engine and ActionExecutor create it in Jira, mark it with our idempotency key
and read it back. A second run must not open a second issue. Each run of this test leaves one
issue in the project.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import cluster_members, fixture

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


def _with_ticketing(text_: str) -> str:
    """Set `ticketing.adapter` to jira_cloud, whatever the comment beside it says."""
    lines = text_.splitlines()
    in_ticketing = False
    for i, line in enumerate(lines):
        if line.strip() == "ticketing:":
            in_ticketing = True
        elif in_ticketing and line.strip().startswith("adapter:"):
            lines[i] = "    adapter: jira_cloud"
            break
    return "\n".join(lines) + "\n"


@pytest.fixture
def app(tmp_path: Path) -> Any:
    settings = Settings()
    if not (
        settings.jira_base_url
        and settings.jira_email
        and settings.jira_api_token
        and settings.jira_project_key
        and settings.test_seller_email
    ):
        pytest.skip("Jira settings or TEST_SELLER_EMAIL are not set (docs/SETUP_CHANNELS.md)")
    modules = (ROOT / "config" / "modules.yaml").read_text(encoding="utf-8")
    (tmp_path / "modules.yaml").write_text(_with_ticketing(modules), encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()
    from dispute_agent.app import create_app

    built = create_app(config_dir=tmp_path, app_env="test", database=database)
    assert type(built.state.application.adapters.ticketing).__name__ == "JiraCloudTicketing"
    return built


def investigation(runtime: Any) -> tuple[str, str | None]:
    with runtime.runner.engine.connect() as conn:
        rows = list(conn.execute(text("SELECT status, external_ref FROM pattern_investigations")))
    assert len(rows) == 1, rows
    return str(rows[0][0]), rows[0][1]


def test_a_recurring_pattern_opens_one_real_jira_investigation(app: Any) -> None:
    client = TestClient(app)
    settings = Settings()
    runtime = app.state.application.runtime
    run = time.time_ns()
    for n, shipment in enumerate(cluster_members(fixture("hub_scanner_cluster"))[:5]):
        body = {
            "source_event_id": f"live-jira-{run}-{n}",
            "shipment_ref": shipment.ref,
            "claim_ref": shipment.claim_ref,
            "seller_ref": shipment.seller_id,
            "seller_contact": settings.test_seller_email,
            "raised_at": "2026-11-04T09:00:00Z",
        }
        assert client.post("/events", json=body).status_code == 200

    [job] = runtime.runner.jobs
    report = job.run(runtime.runner.line.clock.now())
    assert len(report.analysis.created) == 1, "one pattern from five disputes"
    status, ref = investigation(runtime)
    for _ in range(10):  # Jira's read-back can lag a moment; the next run verifies
        if status == "VERIFIED":
            break
        time.sleep(3)
        job.run(runtime.runner.line.clock.now())
        status, ref = investigation(runtime)
    assert status == "VERIFIED", status
    assert ref is not None and ref.startswith(f"{settings.jira_project_key}-")
    print(f"\n  real Jira issue {ref} opened through the executor and read back (VERIFIED)")

    again = job.run(runtime.runner.line.clock.now())
    assert [r.reason for r in again.investigations] == ["already_open"]
    assert investigation(runtime) == ("VERIFIED", ref)
    print(f"  a second analysis run kept {ref}: no duplicate issue")
