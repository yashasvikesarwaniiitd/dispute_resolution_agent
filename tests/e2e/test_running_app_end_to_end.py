"""One dispute through the running app, start to finish - the way it would really happen.

Not the scenario simulator: the app `make run-api` builds (`create_app`, the shipped
modules.yaml with cross_case on), a dispute posted over HTTP, a person deciding in the review
console's own class (`ReviewConsole`), and the background runner's `tick` moving the case.

The case (`calc_error_high_value`): the bill charged a keyed 20,000 g, the parcel really
weighs 7,000 g. Code recalculates, a credit of the difference is proposed, a person approves
it, the simulated ledger posts it and it is read back, a confirmation email is drafted, a
person approves the text, it is sent and read back from Sent Items, the case closes as
RESOLVED_CREDITED, and the closed case is handed to the cross-case observer.

Run with `-s` to see each step narrated.
"""

from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dispute_agent.app import create_app
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import ActorKind, ApprovalSubject
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture

ROOT = Path(__file__).resolve().parents[2]
CASE = fixture("calc_error_high_value")
REVIEWER = Actor(kind=ActorKind.HUMAN, id="human:owner")


def say(text: str) -> None:
    print(f"  - {text}")


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    for name in ("modules.yaml", "policy.yaml"):
        text = (ROOT / "config" / name).read_text(encoding="utf-8")
        (tmp_path / name).write_text(text, encoding="utf-8")
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()
    monkeypatch.setattr(
        "dispute_agent.app.Settings",
        lambda: Settings(app_env="test", test_seller_email=SELLER_CONTACT),
    )
    return create_app(config_dir=tmp_path, app_env="test", database=database)


def state(client: TestClient, case_id: str) -> dict[str, Any]:
    response = client.get(f"/cases/{case_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def approve_pending(app: Any, case_id: str) -> Any:
    """What the reviewer does in the console: read the item, then say yes."""
    runtime = app.state.application.runtime
    with SqliteUnitOfWork(runtime.runner.engine, clock=SystemClock()) as work:
        console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=REVIEWER)
        [pending] = [a for a in console.queue() if str(a.request.case_id) == case_id]
        console.approve(pending.id, "checked against the recalculation")
    return pending


def tick_until(app: Any, client: TestClient, case_id: str, wanted: set[str], ticks: int = 6) -> str:
    runner = app.state.application.runtime.runner
    for _ in range(ticks):
        report = runner.tick()
        assert report.failures == [], report.failures
        current = str(state(client, case_id)["state"])
        if current in wanted:
            return current
        # The real runner ticks every few seconds; Sent Items shows a message after a delay
        # (the fake mailbox imitates Exchange's), so a tick a moment later is the honest model.
        time.sleep(1)
    return current


def test_a_wrongly_keyed_weight_is_credited_confirmed_and_learned_from(app: Any) -> None:
    client = TestClient(app)
    adapters = app.state.application.adapters
    print(f"\nDispute: shipment {CASE.shipment.ref}, billed on 20,000 g, really 7,000 g")

    # 1. The seller disputes the charge.
    body = {
        "source_event_id": "e2e-form-1",
        "shipment_ref": CASE.shipment.ref,
        "claim_ref": CASE.shipment.claim_ref,
        "seller_ref": CASE.shipment.seller_id,
        "seller_contact": SELLER_CONTACT,
        "raised_at": "2026-11-04T09:00:00Z",
    }
    submitted = client.post("/events", json=body).json()
    case_id = submitted["case_id"]
    assert submitted["state"] == "AWAITING_APPROVAL", submitted
    say(f"dispute received as {case_id}; evidence gathered, charge recalculated in code")

    # A second copy of the same form is recognised, not a second case.
    again = client.post("/events", json=body).json()
    assert again["duplicate"] is True and again["case_id"] == case_id
    say("the same form sent twice is recognised as one dispute")

    # 2. A credit waits for a person, with the amount code worked out.
    credit = approve_pending(app, case_id)
    assert credit.request.subject is ApprovalSubject.CREDIT
    amount = credit.request.amount
    assert amount is not None and amount.amount == Decimal("1820.00")
    assert adapters.ledger.credit_count() == 0
    say(f"credit of INR {amount.amount} proposed; nothing posted until a person said yes")
    say("reviewer (human:owner) approved the credit in the review console")

    # 3. The runner posts it, reads it back, and drafts the confirmation for review.
    now = tick_until(app, client, case_id, {"AWAITING_APPROVAL"})
    assert now == "AWAITING_APPROVAL"
    assert adapters.ledger.credit_count() == 1
    assert adapters.email.sent_count() == 0
    say("credit posted to the ledger and read back; confirmation email drafted for review")

    # 4. A person approves the exact text; the runner sends it and reads it back.
    message = approve_pending(app, case_id)
    assert message.request.subject is ApprovalSubject.MESSAGE
    say(f"reviewer approved the {message.request.message_kind.value} email text")
    final = tick_until(app, client, case_id, {"RESOLVED_CREDITED", "NEEDS_HUMAN_REVIEW"}, 10)
    assert final == "RESOLVED_CREDITED", state(client, case_id)
    assert adapters.email.sent_count() == 1
    say("email sent once and found in Sent Items with the approved text")
    say("case closed: RESOLVED_CREDITED (credited and told)")

    # 5. Nothing happens twice, and the closed case reaches the cross-case module.
    for _ in range(3):
        app.state.application.runtime.runner.tick()
    assert adapters.ledger.credit_count() == 1
    assert adapters.email.sent_count() == 1
    say("three more runner ticks: still one credit, one email")
    outcomes = adapters.outcomes.repository.outcomes()
    assert [(c, s) for c, s, _ in outcomes] == [(case_id, "RESOLVED_CREDITED")]
    say("the closed case was handed to the cross-case module (one outcome recorded)")

    timeline = client.get(f"/cases/{case_id}/timeline")
    assert timeline.status_code == 200 and "RESOLVED_CREDITED" in timeline.text
    say("the case timeline page shows the whole history")
