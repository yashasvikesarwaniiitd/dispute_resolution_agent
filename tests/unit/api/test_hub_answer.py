"""The hub's answer on Jira reaches the case and drives its next round (card P3-07).

On the hosted demo WD-56 sat on "waiting for the hub's finding" with a comment under it:
nothing read Jira. Here, on the running app with the fake Jira: a person asks the hub to
remeasure SHP-000001, the hub answers that the parcel is the size the seller declared, and
the hub-findings job carries that into the case. Code then proves the lower remeasurement.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from dispute_agent.api.hub_findings import HubFindingsJob
from dispute_agent.infra.clock import SystemClock
from dispute_agent.kernel.domain.enums import ActionType, ApprovalDecision, CaseState
from dispute_agent.kernel.domain.external import OpsFinding
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.ui.desk import CaseDesk
from dispute_agent.ui.trail import build
from tests.unit.api.test_reopen_dispute import OWNER, _raise, _state, _work, running_app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    return running_app(tmp_path, monkeypatch)


def _issue_key(app: Any, case_id: str) -> str:
    with _work(app) as work:
        [issue] = [
            a
            for a in work.actions.for_case(CaseId(case_id))
            if a.action_type is ActionType.UPSERT_ISSUE
        ]
    assert issue.external_ref
    return str(issue.external_ref)


def _waiting_on_the_hub(app: Any) -> str:
    """Raise SHP-000001 and, as the owner, ask the hub to remeasure."""
    first = _raise(TestClient(app), "console-hub")
    case_id = str(first["case_id"])
    runner = app.state.application.runtime.runner
    with _work(app) as work:
        CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=OWNER).decide(
            work.cases.get(CaseId(case_id)), "request_remeasurement", "Remeasure, please."
        )
    for _ in range(6):
        runner.tick()
        with _work(app) as work:
            for pending in work.approvals.pending(CaseId(case_id)):
                work.approvals.decide(
                    pending.id, ApprovalDecision.APPROVED, OWNER, datetime.now(UTC), "Yes."
                )
        if _state(app, case_id) is CaseState.AWAITING_OPS:
            break
    assert _state(app, case_id) is CaseState.AWAITING_OPS
    return case_id


def _job(app: Any) -> HubFindingsJob:
    runner = app.state.application.runtime.runner
    [job] = [j for j in runner.jobs if isinstance(j, HubFindingsJob)]
    return job


def test_the_running_app_reads_the_hub_s_answers(app: Any) -> None:
    assert _job(app).every.total_seconds() <= 60  # every minute or so


def test_a_remeasurement_on_jira_moves_the_case_and_code_proves_it(app: Any) -> None:
    case_id = _waiting_on_the_hub(app)
    key = _issue_key(app, case_id)
    # The hub answers on the issue: the parcel is the size the seller declared.
    app.state.application.adapters.ticketing.post_finding(
        OpsFinding(
            issue_key=key,
            finding_id="comment:1",
            kind="remeasured",
            measured={
                "length_cm": Decimal(20),
                "width_cm": Decimal(15),
                "height_cm": Decimal(10),
                "dead_grams": 480,
            },
            note=UntrustedText.create(
                "Remeasured: 20 x 15 x 10 cm, 0.48 kg", "jira_comment", f"{key}:1"
            ),
            at=datetime.now(UTC),
        )
    )
    job = _job(app)
    job._last_run = None  # due now, rather than in a minute
    app.state.application.runtime.runner.tick()

    assert _state(app, case_id) is not CaseState.AWAITING_OPS  # the answer moved it on
    with _work(app) as work:
        assessment = work.assessments.latest(CaseId(case_id))
    assert assessment is not None
    assert "remeasurement_lower" in assessment.definite_errors  # code proved it (I9)
    # The timeline says what the hub found, and the figures are the ones the credit uses:
    # it once read "correct ₹152.00, overcharged ₹0.00" beside a ₹76.00 credit (live QA).
    with _work(app) as work:
        steps = build(work, work.cases.get(CaseId(case_id))).steps
    said = [f"{s.title} | {s.summary}" for s in steps]
    assert "The hub sent its finding | Re-measured the parcel: 20 x 15 x 10 cm, 0.48 kg." in said
    assert any("correct ₹76.00, overcharged ₹76.00" in line for line in said)


def test_the_confirmation_email_names_where_its_amount_came_from(app: Any) -> None:
    """Every credit confirmation showed its amount as "NO SOURCE FOUND - do not approve":
    the card read only the assessment's facts, not the credited amount R09 cleared it with."""
    from dispute_agent.modules.approvals.streamlit_ui.console import UNKNOWN_SOURCE, ReviewConsole
    from dispute_agent.ui.review import queue

    case_id = _waiting_on_the_hub(app)
    key = _issue_key(app, case_id)
    app.state.application.adapters.ticketing.post_finding(
        OpsFinding(
            issue_key=key,
            finding_id="comment:2",
            kind="remeasured",
            measured={
                "length_cm": Decimal(20),
                "width_cm": Decimal(15),
                "height_cm": Decimal(10),
                "dead_grams": 480,
            },
            at=datetime.now(UTC),
        )
    )
    runner = app.state.application.runtime.runner
    _job(app)._last_run = None
    cards: list[Any] = []
    for _ in range(20):  # approve the credit, then wait for its confirmation email
        runner.tick()
        with _work(app) as work:
            console = ReviewConsole(store=work.approvals, clock=SystemClock(), reviewer=OWNER)
            cards = [c for c in queue(work, console) if not c.item.is_credit]
            for pending in work.approvals.pending(CaseId(case_id)):
                if pending.request.amount is not None:
                    work.approvals.decide(
                        pending.id, ApprovalDecision.APPROVED, OWNER, datetime.now(UTC), "Yes."
                    )
        if cards:
            break
        time.sleep(0.25)
    assert cards, "no confirmation email reached the queue"
    numbers = cards[0].item.numbers
    assert numbers and all(source != UNKNOWN_SOURCE for _, source in numbers)
    assert ("₹76.00", "amount credited") in numbers


def test_reading_twice_delivers_the_answer_once(app: Any) -> None:
    case_id = _waiting_on_the_hub(app)
    key = _issue_key(app, case_id)
    ticketing = app.state.application.adapters.ticketing
    ticketing.post_finding(
        OpsFinding(
            issue_key=key,
            finding_id="comment:9",
            kind="no_fault_found",
            at=datetime.now(UTC),
        )
    )
    job = _job(app)
    runner = app.state.application.runtime.runner
    for _ in range(2):
        job._last_run = None
        runner.tick()
    with _work(app) as work:
        rows = work.connection.exec_driver_sql(
            "SELECT COUNT(*) FROM inbox WHERE event_type = 'OPS_FINDING_RECEIVED'"
        ).scalar()
    assert rows == 1  # the issue and comment are the dedupe key
