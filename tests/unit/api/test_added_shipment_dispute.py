"""A shipment added on the console is disputed like any other (ADR 0056), on the running app.

Added after the app started, as a person would: the evidence source reads it at once, the
ledger holds its charge, and code proves the overcharge and proposes code's credit, which
waits for a person's approval as every credit does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.modules.evidence.synthetic_shipments.added import AddedShipments
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT
from dispute_agent.ui.add_shipment import save
from tests.unit.api.test_agent_reads_reply import _tick_until
from tests.unit.api.test_reopen_dispute import _state, _work, running_app
from tests.unit.modules.test_added_shipments import kurta

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    return running_app(tmp_path, monkeypatch)


def _raise(app: Any, ref: str) -> str:
    from fastapi.testclient import TestClient

    response = TestClient(app).post(
        "/events",
        json={
            "source_event_id": f"console-{ref}",
            "shipment_ref": ref,
            "claim_ref": ref.replace("SHP", "CLM"),
            "seller_ref": "SELL-0001",
            "seller_contact": SELLER_CONTACT,
            "raised_at": datetime.now(UTC).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["case_id"])


def test_an_overcharged_added_shipment_ends_in_code_s_credit(app: Any, tmp_path: Path) -> None:
    # The app's data folder is the case database's (create_app: data_dir = database.parent).
    save(
        AddedShipments(tmp_path / "shipments.sqlite3"),
        tmp_path / "ledger.sqlite3",
        kurta(),  # billed ₹190 where the rate card gives ₹152
        None,  # no photo: optional
        datetime.now(UTC),
    )
    case_id = _raise(app, "SHP-100001")
    got = _tick_until(app, case_id, {CaseState.AWAITING_APPROVAL, CaseState.NEEDS_HUMAN_REVIEW})
    assert got is CaseState.AWAITING_APPROVAL
    with _work(app) as work:
        [credit] = list(work.approvals.pending(CaseId(case_id)))
    assert credit.request.amount is not None and str(credit.request.amount.amount) == "38.00"


def test_an_undelivered_added_shipment_is_not_eligible(app: Any, tmp_path: Path) -> None:
    save(
        AddedShipments(tmp_path / "shipments.sqlite3"),
        tmp_path / "ledger.sqlite3",
        kurta(status="RTO"),
        None,
        datetime.now(UTC),
    )
    case_id = _raise(app, "SHP-100001")
    _tick_until(app, case_id, {CaseState.REJECTED_INELIGIBLE})
    assert _state(app, case_id) is CaseState.REJECTED_INELIGIBLE
