"""P6-08: the threat-model gaps the security review found, each closed with a test.

T03 a reply that failed sender authentication escalates; T04 a changed recipient on read-back
is never verified; T09 only an approver on the list can decide; T15 an unreadable kill switch
counts as on.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import create_engine

from dispute_agent.api.full_run import REVIEWER
from dispute_agent.infra.sqlite.control_flags import SqliteKillSwitch
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    ReviewCode,
)
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.inbound import SellerReplyReceived
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT


def review(world: Any) -> tuple[CaseState, Any]:
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        case = work.cases.get(world.case_id)
    return case.state, case.review_reason


class TestT09OnlyAnApproverCanDecide:
    def test_a_decision_by_a_person_not_on_the_list_moves_no_money(self, stage: Any) -> None:
        world = stage("calc_error_wrong_slab", ["dispute_raised@0h"], ["propose_credit"]).world
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            [pending] = work.approvals.pending(world.case_id)
            work.approvals.decide(
                pending.id,
                ApprovalDecision.APPROVED,
                Actor(kind=ActorKind.HUMAN, id="human:someone-else"),
                world.clock.now(),
                "",
            )
        world.drive()
        state, reason = review(world)
        assert state is CaseState.NEEDS_HUMAN_REVIEW
        assert reason is not None and "not an approver" in (reason.detail or "")
        assert world.ledger.post_calls == 0

    def test_the_owner_on_the_list_still_can(self, stage: Any) -> None:
        """The control."""
        world = stage("calc_error_wrong_slab", ["dispute_raised@0h"], ["propose_credit"]).world
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            [pending] = work.approvals.pending(world.case_id)
            work.approvals.decide(
                pending.id, ApprovalDecision.APPROVED, REVIEWER, world.clock.now(), ""
            )
        world.drive()
        assert world.ledger.credit_count() == 1


class TestT03AnUnauthenticatedSenderEscalates:
    def test_a_reply_that_failed_dmarc_sends_the_case_to_a_person(self, stage: Any) -> None:
        played = stage(
            "kurta_box_larger",
            ["dispute_raised@0h", "approve(evidence_request)@1h"],
            ["request_seller_evidence", "uphold_charge"],
        )
        world = played.world
        # The next day, so the daily contact limit (R05) is not what stops the reply round.
        world.clock.advance(timedelta(hours=24))
        mid = "spoofed-1"
        world.runner.submit(
            InboundEvent(
                type=InboundEventType.SELLER_REPLY_RECEIVED,
                source="outlook",
                source_event_id=mid,
                occurred_at=world.clock.now(),
                actor=Actor(kind=ActorKind.EXTERNAL, id="seller"),
                payload=SellerReplyReceived(
                    external_message_id=mid,
                    from_addr=SELLER_CONTACT,
                    subject=UntrustedText.create(
                        f"Re: {world.report().case.facts.shipment_ref}", "seller_email", mid
                    ),
                    body=UntrustedText.create(
                        "[reply:photo_shows_large_box] here it is", "seller_email", mid
                    ),
                    received_at=world.clock.now(),
                    sender_auth="fail",
                ),
            )
        )
        world.drive()
        state, reason = review(world)
        assert state is CaseState.NEEDS_HUMAN_REVIEW
        assert reason is not None and reason.code is ReviewCode.SENDER_UNAUTHENTICATED


class TestT04ARecipientChangedOnTheWayIsNeverVerified:
    def test_a_read_back_to_someone_else_is_unknown_not_verified(
        self, stage: Any, monkeypatch: Any
    ) -> None:
        real = FakeMessageChannel.get_message

        def elsewhere(self: Any, receipt: Any) -> Any:
            snapshot = real(self, receipt)
            return snapshot.model_copy(update={"to": ("someone@else.invalid",)})

        monkeypatch.setattr(FakeMessageChannel, "get_message", elsewhere)
        world = stage(
            "kurta_box_larger",
            ["dispute_raised@0h", "approve(evidence_request)@1h"],
            ["request_seller_evidence"],
        ).world
        state, reason = review(world)
        assert state is CaseState.NEEDS_HUMAN_REVIEW
        assert reason is not None and reason.code is ReviewCode.VERIFICATION_FAILED


class TestT15AnUnreadableSwitchIsOn:
    def test_a_switch_that_cannot_be_read_counts_as_engaged(self, tmp_path: Any) -> None:
        engine = create_engine(f"sqlite:///{tmp_path / 'no-table.sqlite3'}")
        assert SqliteKillSwitch(engine).is_engaged() is True  # no control_flags table at all

    def test_a_readable_switch_reports_what_it_holds(self, stage: Any) -> None:
        world = stage("kurta_box_larger", ["dispute_raised@0h"], ["request_seller_evidence"]).world
        switch = SqliteKillSwitch(world.engine)
        assert switch.is_engaged() is False
        switch.set(True, "human:owner", world.clock.now())
        assert switch.is_engaged() is True
        switch.set(False, "human:owner", world.clock.now())
        assert switch.is_engaged() is False
