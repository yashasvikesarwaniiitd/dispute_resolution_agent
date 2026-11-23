"""The agent reads the seller's reply and acts on it (ADR 0054), on the running app with fakes.

The whole loop, with a stand-in for the model: a person asks the seller for evidence; the
seller replies; the reader reads it as the evidence asked for; the agent itself asks the hub
to remeasure (B4a), with no person in between; the hub answers smaller; code proves the
overcharge and proposes the credit, which still waits for a person's approval.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.api.hub_findings import HubFindingsJob
from dispute_agent.api.seller_replies import SellerRepliesJob
from dispute_agent.infra.clock import SystemClock
from dispute_agent.kernel.domain.enums import ActionType, ApprovalDecision, CaseState
from dispute_agent.kernel.domain.external import InboundMessage, OpsFinding
from dispute_agent.kernel.domain.ids import CaseId, PayloadHash
from dispute_agent.kernel.domain.reasoning import ReplyContext, ReplyReading, RequestedItemStatus
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT
from dispute_agent.ui.desk import CaseDesk
from dispute_agent.ui.trail import build
from tests.unit.api.test_reopen_dispute import OWNER, _raise, _state, _work, running_app

if TYPE_CHECKING:
    from pathlib import Path

ASKED = "Photo of the packed box beside a tape measure"


class StandInReader:
    """Reads every reply as the evidence asked for. The real one is the live eval's job."""

    def __init__(self) -> None:
        self.read: list[ReplyContext] = []

    def interpret(self, ctx: ReplyContext) -> ReplyReading:
        self.read.append(ctx)
        return ReplyReading(
            intent="provides_requested_evidence",
            requested_items_status=tuple(
                RequestedItemStatus(item=i, status="provided") for i in ctx.requested_items
            ),
            summary="Seller sends the photo asked for.",
            language="en",
            prompt_name="seller_reply_interpreter",
            prompt_version="v1",
            prompt_hash=PayloadHash("sha256:" + "0" * 64),
        )


@pytest.fixture
def reader() -> StandInReader:
    return StandInReader()


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reader: StandInReader) -> Any:
    monkeypatch.setattr("dispute_agent.app.build_reply_reader", lambda *_a, **_k: reader)
    return running_app(tmp_path, monkeypatch)


def _tick_until(app: Any, case_id: str, wanted: set[CaseState], tries: int = 30) -> CaseState:
    runner = app.state.application.runtime.runner
    for _ in range(tries):
        runner.tick()
        with _work(app) as work:
            for pending in work.approvals.pending(CaseId(case_id)):
                if pending.request.amount is None:  # emails: approved; a credit: left waiting
                    work.approvals.decide(
                        pending.id, ApprovalDecision.APPROVED, OWNER, datetime.now(UTC), "Yes."
                    )
        if _state(app, case_id) in wanted:
            break
        time.sleep(0.25)
    return _state(app, case_id)


def _job(app: Any, kind: type) -> Any:
    [job] = [j for j in app.state.application.runtime.runner.jobs if isinstance(j, kind)]
    return job


def test_the_agent_reads_the_reply_asks_the_hub_and_code_proves_the_credit(
    app: Any, reader: StandInReader
) -> None:
    from fastapi.testclient import TestClient

    case_id = str(_raise(TestClient(app), "console-agent")["case_id"])
    with _work(app) as work:
        CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=OWNER).decide(
            work.cases.get(CaseId(case_id)),
            "request_seller_evidence",
            "Ask for a photo.",
            requested_items=(ASKED,),
        )
    assert _tick_until(app, case_id, {CaseState.AWAITING_SELLER}) is CaseState.AWAITING_SELLER

    # The seller replies; the agent reads it and, with no person, asks the hub (B4a).
    app.state.application.adapters.email.deliver(
        InboundMessage(
            external_message_id="reply-agent-1",
            internet_message_id="<reply-agent-1@mail.example.invalid>",
            from_addr=SELLER_CONTACT,
            to_addr="support@example.invalid",
            subject=UntrustedText.create("Re: SHP-000001", "seller_email", "reply-agent-1"),
            body=UntrustedText.create(
                "Photo attached, box beside a tape measure.", "seller_email", "reply-agent-1"
            ),
            received_at=datetime.now(UTC),
        )
    )
    _job(app, SellerRepliesJob)._last_run = None
    assert _tick_until(app, case_id, {CaseState.AWAITING_OPS}) is CaseState.AWAITING_OPS
    assert [c.requested_items for c in reader.read] == [(ASKED,)]  # it read the reply once
    with _work(app) as work:
        case = work.cases.get(CaseId(case_id))
        proposals = list(work.proposals.for_case(case.id))
        [issue] = [
            a for a in work.actions.for_case(case.id) if a.action_type is ActionType.UPSERT_ISSUE
        ]
        steps = build(work, case).steps
    assert proposals[-1].action.value == "request_remeasurement"
    assert proposals[-1].reasoner != "human"  # the agent's own step, not a person's
    [seller] = [s for s in steps if s.title == "The seller replied"]
    assert "The agent read the reply: the seller sent the evidence we asked for" in seller.summary

    # The hub measures the parcel as the seller declared; code proves the overcharge.
    app.state.application.adapters.ticketing.post_finding(
        OpsFinding(
            issue_key=str(issue.external_ref),
            finding_id="comment:agent",
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
    _job(app, HubFindingsJob)._last_run = None
    got = _tick_until(app, case_id, {CaseState.AWAITING_APPROVAL})
    assert got is CaseState.AWAITING_APPROVAL
    with _work(app) as work:
        [credit] = list(work.approvals.pending(CaseId(case_id)))
    assert credit.request.amount is not None and str(credit.request.amount.amount) == "76.00"


def test_a_real_gmail_reply_is_read_not_flagged(app: Any, reader: StandInReader) -> None:
    """ADR 0055, the live fault: a reply from Gmail quotes our email under the seller's words,
    and its "Name <address> wrote:" line made the input screen flag every such reply, so the
    case went to a person as an instruction attempt. Now only the seller's words are read."""
    from fastapi.testclient import TestClient

    own = "Here is the photo of the packed box beside a tape measure."
    gmail = (
        f"{own}\r\n\r\nOn Sun, 23 Nov 2026 at 11:10, Dispute desk <\r\n"
        "support@example.invalid> wrote:\r\n\r\n> Hello,\r\n>\r\n"
        "> Please reply to this email with a photo of the packed box.\r\n"
    )
    case_id = str(_raise(TestClient(app), "console-gmail")["case_id"])
    with _work(app) as work:
        CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=OWNER).decide(
            work.cases.get(CaseId(case_id)),
            "request_seller_evidence",
            "Ask for a photo.",
            requested_items=(ASKED,),
        )
    assert _tick_until(app, case_id, {CaseState.AWAITING_SELLER}) is CaseState.AWAITING_SELLER
    app.state.application.adapters.email.deliver(
        InboundMessage(
            external_message_id="reply-gmail-1",
            internet_message_id="<reply-gmail-1@mail.example.invalid>",
            from_addr=SELLER_CONTACT,
            to_addr="support@example.invalid",
            subject=UntrustedText.create("Re: SHP-000001", "seller_email", "reply-gmail-1"),
            body=UntrustedText.create(gmail, "seller_email", "reply-gmail-1"),
            received_at=datetime.now(UTC),
        )
    )
    _job(app, SellerRepliesJob)._last_run = None
    got = _tick_until(app, case_id, {CaseState.AWAITING_OPS, CaseState.NEEDS_HUMAN_REVIEW})
    assert got is CaseState.AWAITING_OPS  # the agent acted; no false flag sent it to a person
    assert [c.reply.text for c in reader.read] == [own]  # the reader saw the seller's words only
