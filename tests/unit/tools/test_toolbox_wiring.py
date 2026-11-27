"""ADR 0071: the running app assembles one tool per capability from the modules that are on,
and the past-disputes tool reads the real case table."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi.testclient import TestClient

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.tools import ToolContext
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT
from tests.unit.api.test_full_runtime import FULL_LINE
from tests.unit.api.test_reopen_dispute import running_app

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

ALL_ON = FULL_LINE.replace(
    'cross_case:    {enabled: false, adapter: "null"}',
    "cross_case:    {enabled: true,  adapter: sql_context}",
).replace(
    "image_check:   {enabled: false, adapter: recorded}",
    "image_check:   {enabled: true,  adapter: recorded}",
)
FIVE = {
    "lookup_policy",
    "search_past_shipments",
    "get_scanner_statistics",
    "search_past_disputes",
    "analyze_parcel_image",
}


def app_with(modules: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr("tests.unit.api.test_reopen_dispute.FULL_LINE", modules)
    return running_app(tmp_path, monkeypatch)


def facts(ref: str) -> CaseFacts:
    return CaseFacts(
        shipment_ref=ref,
        claim_ref=ref.replace("SHP", "CLM"),
        seller_ref="SELL-0001",
        seller_contact=SELLER_CONTACT,
        raised_at=datetime.now(UTC),
    )


def test_all_five_when_every_module_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = app_with(ALL_ON, tmp_path, monkeypatch)
    tools = app.state.application.runtime.tools
    assert set(tools.names) == FIVE

    # A dispute in the real table is found by the next dispute from the same seller.
    for n, ref in enumerate(("SHP-000001", "SHP-000002")):
        TestClient(app).post(
            "/events",
            json={
                "source_event_id": f"e{n}",
                "shipment_ref": ref,
                "claim_ref": ref.replace("SHP", "CLM"),
                "seller_ref": "SELL-0001",
                "seller_contact": SELLER_CONTACT,
                "raised_at": datetime.now(UTC).isoformat(),
            },
        ).raise_for_status()
    out = tools.call(
        "search_past_disputes", ToolContext(case_id=CaseId("c"), facts=facts("SHP-000002"))
    )
    assert out.ok, out
    assert [d["shipment_ref"] for d in out.data["this_seller"]] == ["SHP-000001"]


def test_a_module_that_is_off_brings_no_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = app_with(FULL_LINE, tmp_path, monkeypatch)  # cross_case and image_check off
    names = set(app.state.application.runtime.tools.names)
    assert names == FIVE - {"search_past_disputes", "analyze_parcel_image"}
