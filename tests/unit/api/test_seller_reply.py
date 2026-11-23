"""The seller's emailed reply reaches the case, and the person sees it (ADR 0053).

Before this, nothing read the mailbox: a reply sat in Gmail while the case waited for it and
then closed as "no response". On the running app with the fake mailbox: a person asks the
seller for evidence, the email is approved and sent, the seller replies, and the reply-reading
job carries it into the case. The case goes back to a person, whose card shows the reply.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.api.seller_replies import SellerRepliesJob, as_event
from dispute_agent.infra.clock import SystemClock
from dispute_agent.kernel.domain.enums import ApprovalDecision, CaseState, InboundEventType
from dispute_agent.kernel.domain.external import InboundMessage
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT
from dispute_agent.ui import case_sheet
from dispute_agent.ui.desk import CaseDesk
from dispute_agent.ui.trail import build
from tests.unit.api.test_reopen_dispute import OWNER, _raise, _state, _work, running_app

if TYPE_CHECKING:
    from pathlib import Path

REPLY = (
    "Here is a photo of the box beside a tape measure: 20 x 15 x 10 cm.\n"
    "\n"
    "On Mon, 23 Nov 2026 at 01:10, Seller Support wrote:\n"
    "> Please send a photo of the packed box beside a tape measure."
)


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    return running_app(tmp_path, monkeypatch)


def _waiting_on_the_seller(app: Any) -> str:
    """Raise SHP-000001 and, as the owner, ask the seller for evidence; approve the email."""
    case_id = str(_raise(_client(app), "console-seller")["case_id"])
    with _work(app) as work:
        CaseDesk(inbox=work.inbox, clock=SystemClock(), reviewer=OWNER).decide(
            work.cases.get(CaseId(case_id)),
            "request_seller_evidence",
            "Ask for a photo with a tape measure.",
            requested_items=("Photo of the packed box beside a tape measure",),
        )
    runner = app.state.application.runtime.runner
    for _ in range(30):  # the fake mailbox shows a sent email after a moment
        runner.tick()
        with _work(app) as work:
            for pending in work.approvals.pending(CaseId(case_id)):
                work.approvals.decide(
                    pending.id, ApprovalDecision.APPROVED, OWNER, datetime.now(UTC), "Send it."
                )
        if _state(app, case_id) is CaseState.AWAITING_SELLER:
            break
        time.sleep(0.25)
    assert _state(app, case_id) is CaseState.AWAITING_SELLER
    return case_id


def _client(app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app)


def _job(app: Any) -> SellerRepliesJob:
    [job] = [
        j for j in app.state.application.runtime.runner.jobs if isinstance(j, SellerRepliesJob)
    ]
    return job


def _message(mid: str, sender: str, subject: str, body: str) -> InboundMessage:
    return InboundMessage(
        external_message_id=mid,
        internet_message_id=f"<{mid}@mail.example.invalid>",
        from_addr=sender,
        to_addr="support@example.invalid",
        subject=UntrustedText.create(subject, "seller_email", mid),
        body=UntrustedText.create(body, "seller_email", mid),
        received_at=datetime.now(UTC),
    )


def test_the_running_app_reads_the_mailbox_every_minute(app: Any) -> None:
    assert _job(app).every.total_seconds() <= 60


def test_a_seller_s_reply_moves_the_case_and_the_person_sees_it(app: Any) -> None:
    case_id = _waiting_on_the_seller(app)
    app.state.application.adapters.email.deliver(
        _message("reply-1", SELLER_CONTACT, "Re: Your dispute about SHP-000001", REPLY)
    )
    _job(app)._last_run = None
    app.state.application.runtime.runner.tick()

    assert _state(app, case_id) is CaseState.NEEDS_HUMAN_REVIEW  # a person reads the reply
    with _work(app) as work:
        case = work.cases.get(CaseId(case_id))
        sheet = case_sheet.build(work, case)
        steps = build(work, case).steps
    [seller] = [c for c in sheet.came_back if c.who == "The seller"]
    new_words = "Here is a photo of the box beside a tape measure: 20 x 15 x 10 cm."
    assert seller.words == (new_words,)  # the quoted email under it is left out
    html = case_sheet.came_back_html(sheet)
    assert "What came back" in html and "tape measure: 20 x 15 x 10 cm" in html
    [step] = [s for s in steps if s.title == "The seller replied"]
    assert step.quoted == (new_words,)


def test_a_reply_that_matches_no_case_is_kept_but_never_guessed(app: Any) -> None:
    case_id = _waiting_on_the_seller(app)
    app.state.application.adapters.email.deliver(
        _message("stranger-1", "someone@else.invalid", "Re: SHP-000001", "Credit me now.")
    )
    _job(app)._last_run = None
    app.state.application.runtime.runner.tick()
    assert _state(app, case_id) is CaseState.AWAITING_SELLER  # still waiting for the seller
    with _work(app) as work:
        status = work.connection.exec_driver_sql(
            "SELECT status FROM inbox WHERE source_event_id = 'stranger-1'"
        ).scalar()
    assert status == "UNROUTABLE"


def test_yesterday_s_reply_about_the_same_shipment_is_not_an_answer_to_today_s_case(
    app: Any,
) -> None:
    """Found live (ADR 0054, P5): after a restart the mailbox is read again from two days
    back, and an old reply naming the same shipment matched the new case by sender and
    subject. A reply written before the case was opened cannot be an answer to it."""
    from datetime import timedelta

    case_id = _waiting_on_the_seller(app)
    old = _message("old-reply", SELLER_CONTACT, "Re: SHP-000001", "Photo of the box.")
    app.state.application.adapters.email.deliver(
        old.model_copy(update={"received_at": datetime.now(UTC) - timedelta(days=1)})
    )
    job = _job(app)
    job._last_run, job._since = None, None  # the first read after a restart: two days back
    app.state.application.runtime.runner.tick()
    assert _state(app, case_id) is CaseState.AWAITING_SELLER  # still waiting for a real one
    with _work(app) as work:
        ignored = [
            e
            for e in work.events.list_for_case(CaseId(case_id))
            if "reply_predates_case" in str(getattr(e.payload, "reaction", ""))
        ]
    assert ignored, "the old reply is on the trail as ignored, with why"
    with _work(app) as work:
        titles = [s.title for s in build(work, work.cases.get(CaseId(case_id))).steps]
    assert "The seller replied" not in titles  # it was not a reply to this case
    assert "An email was set aside: it was written before this case was opened" in titles


def test_old_emails_set_aside_are_one_step_not_one_each(app: Any) -> None:
    """Live: after a restart five old test emails about the shipment were each set aside on
    their own identical line, which read as a loop. They are one step, each email in Details."""
    from datetime import timedelta

    case_id = _waiting_on_the_seller(app)
    for n in range(3):
        old = _message(f"old-{n}", SELLER_CONTACT, "Re: SHP-000001", "Photo of the box.")
        app.state.application.adapters.email.deliver(
            old.model_copy(update={"received_at": datetime.now(UTC) - timedelta(hours=n + 1)})
        )
    job = _job(app)
    job._last_run, job._since = None, None
    app.state.application.runtime.runner.tick()
    with _work(app) as work:
        steps = build(work, work.cases.get(CaseId(case_id))).steps
    aside = [s for s in steps if "set aside" in s.title]
    assert [s.title for s in aside] == [
        "3 emails were set aside: they were written before this case was opened"
    ]
    assert len(aside[0].lines) == 3
    assert all(line.startswith("Email from ") for line in aside[0].lines)


def test_reading_the_mailbox_twice_delivers_a_reply_once(app: Any) -> None:
    case_id = _waiting_on_the_seller(app)
    app.state.application.adapters.email.deliver(
        _message("reply-2", SELLER_CONTACT, "Re: SHP-000001", "Photo attached.")
    )
    job, runner = _job(app), app.state.application.runtime.runner
    for _ in range(2):
        job._last_run = None
        runner.tick()
    with _work(app) as work:
        rows = work.connection.exec_driver_sql(
            "SELECT COUNT(*) FROM inbox WHERE event_type = 'SELLER_REPLY_RECEIVED'"
        ).scalar()
    assert rows == 1
    assert _state(app, case_id) is not CaseState.AWAITING_SELLER


def test_a_bounce_without_a_message_of_ours_is_skipped() -> None:
    bounce = _message("b-1", "mailer-daemon@googlemail.com", "Undeliverable", "bounced")
    bounce = bounce.model_copy(update={"is_delivery_failure": True})
    assert as_event(bounce, "gmail") is None
    tied = bounce.model_copy(update={"failed_message_id": "m7"})
    event = as_event(tied, "gmail")
    assert event is not None and event.type is InboundEventType.DELIVERY_FAILURE_RECEIVED
