"""The inbound half of the line: replies, timers, decisions, withdrawals, findings, restarts.

Each test drives a synthetic case on the whole line (`open_world`), then sends it what the
outside world would - and checks the path, the effects and the trail against transitions.yaml
and orchestrator.md sections 3, 6 and 7.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dispute_agent.api.full_run import open_world
from dispute_agent.api.runner import Runner
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    IntentStatus,
    InternalEventType,
    ReviewCode,
    TimerKind,
)
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.external import OpsFinding
from dispute_agent.kernel.domain.inbound import (
    ApprovalDecided,
    DisputeWithdrawn,
    OpsFindingReceived,
    SellerReplyReceived,
    TimerFired,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT

S = CaseState
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
HOURS = timedelta(hours=1)
OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")


def world(name: str = "scanner_fault", **kwargs: Any) -> Any:
    return open_world(
        name, policy=PolicyConfig(), clock=FakeClock(NOW), ids=FakeIdGenerator(), **kwargs
    )


def awaiting_seller() -> Any:
    w = world()
    assert w.drive().case.state is S.AWAITING_SELLER
    return w


def events(w: Any) -> list[Any]:
    with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
        return list(work.events.list_for_case(w.case_id))


def active_timers(w: Any) -> set[TimerKind]:
    with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
        return {t.kind for t in work.timers.active_for_case(w.case_id)}


def inbound(kind: InboundEventType, source: str, sid: str, payload: Any, **kw: Any) -> Any:
    return InboundEvent(
        type=kind,
        source=source,
        source_event_id=sid,
        occurred_at=kw.pop("at", NOW),
        actor=kw.pop("actor", Actor(kind=ActorKind.EXTERNAL, id=source)),
        payload=payload,
        **kw,
    )


class TestTheSellerWindowOpensWithTheRequest:
    def test_a_verified_request_arms_the_reminder_and_the_decision_timers(self) -> None:
        """T18's effect: SELLER_REMINDER (48 h) and SELLER_DECISION (72 h)."""
        w = awaiting_seller()
        try:
            assert active_timers(w) == {TimerKind.SELLER_REMINDER, TimerKind.SELLER_DECISION}
            assert any(e.type is InternalEventType.TIMER_SCHEDULED for e in events(w))
        finally:
            w.close()


class TestASilentSeller:
    def test_a_reminder_after_48_hours_then_closed_after_72(self) -> None:
        w = awaiting_seller()
        try:
            w.clock.advance(48 * HOURS + HOURS)
            w.tick()  # SELLER_REMINDER fires -> the reminder step -> awaits approval (T27)
            assert w.report().case.state is S.AWAITING_APPROVAL
            assert w.drive().case.state is S.AWAITING_SELLER  # approved, sent, verified (T19)
            assert w.report().emails_sent == 2
            # T19 does not reset the window: the decision timer is the original one.
            w.clock.advance(24 * HOURS)
            w.tick()
            report = w.report()
            assert report.case.state is S.CLOSED_NO_RESPONSE  # T29
            assert any(e.type is InternalEventType.CASE_OUTCOME_RECORDED for e in events(w))
            assert active_timers(w) == set()  # cancel_timers: all
        finally:
            w.close()

    def test_the_reminder_asks_for_exactly_what_the_request_asked_for(self) -> None:
        w = awaiting_seller()
        try:
            w.clock.advance(49 * HOURS)
            w.tick()
            [reminder] = w.report().pending_payloads
            first = w.report().proposals[-1].action_parameters.requested_items
            assert first and all(item in reminder.body for item in first)
        finally:
            w.close()


class TestASellerReply:
    def test_a_reply_reopens_the_evidence_round_and_is_kept_as_untrusted_evidence(self) -> None:
        """T26. With no interpreter the reply cannot be read, so a person decides (B2)."""
        w = awaiting_seller()
        try:
            result = w.reply("Here is a photo of the parcel on a scale.")
            assert result.reaction == "transition:seller_reply_received"
            report = w.report()
            assert S.GATHERING_EVIDENCE in report.path[report.path.index(S.AWAITING_SELLER) :]
            assert report.case.state is S.NEEDS_HUMAN_REVIEW
            with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
                bundle = work.evidence.bundle(w.case_id, report.case.gather_round)
            [item] = [i for i in bundle.items if i.result.requirement_key == "seller_reply"]
            assert item.result.error_code == "reply_unreadable:no_interpreter"
            assert item.result.untrusted[0].text.startswith("Here is a photo")
        finally:
            w.close()

    def test_the_same_reply_twice_is_one_reply(self) -> None:
        w = awaiting_seller()
        try:
            w.reply("photo attached", message_id="m-1")
            again = w.reply("photo attached", message_id="m-1")
            assert again.reaction.startswith("already:")
        finally:
            w.close()

    def test_a_reply_from_someone_else_is_kept_but_routed_nowhere(self) -> None:
        w = awaiting_seller()
        try:
            stranger = inbound(
                InboundEventType.SELLER_REPLY_RECEIVED,
                "outlook",
                "stranger-1",
                SellerReplyReceived(
                    external_message_id="stranger-1",
                    from_addr="someone@else.invalid",
                    subject=UntrustedText.create("Re: SHP", "seller_email", "s"),
                    body=UntrustedText.create("refund me", "seller_email", "s"),
                    received_at=NOW,
                ),
            )
            result = w.runner.submit(stranger)
            assert result.reaction.startswith("unroutable:")
            assert w.report().case.state is S.AWAITING_SELLER
        finally:
            w.close()

    def test_a_reply_the_seller_sent_means_the_deadline_never_closes_as_silent(self) -> None:
        """D19: a seller who replied is never closed as no response."""
        w = awaiting_seller()
        try:
            w.reply("I measured it myself, it is lighter")
            w.clock.advance(80 * HOURS)
            w.tick()
            assert w.report().case.state is not S.CLOSED_NO_RESPONSE
        finally:
            w.close()


class TestReopeningAfterClosing:
    def test_a_late_reply_inside_the_window_reopens_the_case(self) -> None:
        w = awaiting_seller()
        try:
            w.clock.advance(49 * HOURS)
            w.tick()
            w.drive()
            w.clock.advance(24 * HOURS)
            w.tick()
            assert w.report().case.state is S.CLOSED_NO_RESPONSE
            w.clock.advance(24 * HOURS)  # inside the 7-day window
            w.reply("sorry, I was away - photos attached")
            report = w.report()
            assert report.case.reopen_count == 1
            assert S.GATHERING_EVIDENCE in report.path[report.path.index(S.CLOSED_NO_RESPONSE) :]
        finally:
            w.close()

    def test_a_late_reply_outside_the_window_goes_to_a_person(self) -> None:
        w = awaiting_seller()
        try:
            w.clock.advance(49 * HOURS)
            w.tick()
            w.drive()
            w.clock.advance(24 * HOURS)
            w.tick()
            w.clock.advance(timedelta(days=8))
            w.reply("here you go")
            case = w.report().case
            assert case.state is S.NEEDS_HUMAN_REVIEW
            assert case.review_reason.code is ReviewCode.LATE_INBOUND
        finally:
            w.close()


class TestApprovals:
    def test_an_undecided_approval_expires_and_the_plan_with_it(self) -> None:
        w = world("calc_error_wrong_slab", decide=lambda a: None)
        try:
            assert w.drive().case.state is S.AWAITING_APPROVAL
            w.clock.advance(73 * HOURS)
            w.tick()
            report = w.report()
            assert report.case.state is S.NEEDS_HUMAN_REVIEW
            assert report.case.review_reason.code is ReviewCode.APPROVAL_EXPIRED
            [approval] = report.approvals
            assert approval.decision is ApprovalDecision.EXPIRED
            with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
                intent = work.intents.get(approval.request.intent_id)
            assert intent.status is IntentStatus.EXPIRED
            assert report.ledger_credits == 0
        finally:
            w.close()

    def _decision(self, w: Any, approval: Any, payload_hash: str) -> Any:
        return inbound(
            InboundEventType.APPROVAL_DECIDED,
            "approvals",
            f"{approval.id}:decision:{payload_hash[-6:]}",
            ApprovalDecided(
                approval_id=approval.id, decision="APPROVED", payload_hash_seen=payload_hash
            ),
            actor=OWNER,
            case_id=w.case_id,
        )

    def test_a_decision_on_text_the_reviewer_did_not_see_is_ignored(self) -> None:
        """I13 at dispatch: payload_hash_seen must equal the approval's."""
        w = world("calc_error_wrong_slab", decide=lambda a: None)
        try:
            w.drive()
            [approval] = w.report().approvals
            result = w.runner.submit(self._decision(w, approval, "sha256:" + "0" * 64))
            assert result.reaction == "ignore:payload_hash_mismatch"
            assert w.report().ledger_credits == 0
        finally:
            w.close()

    def test_a_decision_event_on_the_right_text_moves_the_case(self) -> None:
        w = world("calc_error_wrong_slab", decide=lambda a: None)
        try:
            w.drive()
            [approval] = w.report().approvals
            result = w.runner.submit(self._decision(w, approval, approval.request.payload_hash))
            assert result.reaction == "step:apply_approval"
            report = w.report()
            # Credit posted and verified; the case now waits on the confirmation's approval.
            assert report.ledger_credits == 1
            assert [a.request.subject.value for a in report.approvals] == ["CREDIT", "MESSAGE"]
        finally:
            w.close()

    def test_a_decision_from_a_non_human_is_ignored(self) -> None:
        w = world("calc_error_wrong_slab", decide=lambda a: None)
        try:
            w.drive()
            [approval] = w.report().approvals
            event = self._decision(w, approval, approval.request.payload_hash).model_copy(
                update={"actor": Actor(kind=ActorKind.SYSTEM, id="bot")}
            )
            assert w.runner.submit(event).reaction == "ignore:not_human"
        finally:
            w.close()


class TestWithdrawal:
    def _withdraw(self, w: Any, principal: str) -> Any:
        case = w.report().case
        return inbound(
            InboundEventType.DISPUTE_WITHDRAWN,
            "seller_portal",
            f"wd-{principal}",
            DisputeWithdrawn(
                shipment_ref=case.facts.shipment_ref,
                claim_ref=case.facts.claim_ref,
                seller_ref=case.facts.seller_ref,
                withdrawn_at=NOW,
                authenticated_principal=principal,
            ),
        )

    def test_the_seller_s_own_withdrawal_closes_the_case(self) -> None:
        w = awaiting_seller()
        try:
            seller = w.report().case.facts.seller_ref
            assert w.runner.submit(self._withdraw(w, seller)).state is S.WITHDRAWN
        finally:
            w.close()

    def test_a_withdrawal_by_anyone_else_is_ignored(self) -> None:
        w = awaiting_seller()
        try:
            result = w.runner.submit(self._withdraw(w, "SLR-SOMEONE-ELSE"))
            assert result.reaction == "ignore:principal_mismatch"
            assert w.report().case.state is S.AWAITING_SELLER
        finally:
            w.close()


class TestStaleTimers:
    def test_a_timer_cancelled_by_a_move_is_ignored_if_it_arrives_anyway(self) -> None:
        """P1: a withdrawn case's reminder must not run the reminder step."""
        w = awaiting_seller()
        try:
            with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
                [reminder] = [
                    t
                    for t in work.timers.active_for_case(w.case_id)
                    if t.kind is TimerKind.SELLER_REMINDER
                ]
            w.runner.submit(TestWithdrawal()._withdraw(w, w.report().case.facts.seller_ref))
            late = inbound(
                InboundEventType.TIMER_FIRED,
                "scheduler",
                str(reminder.id),
                TimerFired(timer_id=reminder.id, kind=reminder.kind, due_at=reminder.due_at),
                actor=Actor(kind=ActorKind.SYSTEM, id="scheduler"),
                case_id=w.case_id,
            )
            assert w.runner.submit(late).reaction == "ignore:stale_timer"
            assert w.report().case.state is S.WITHDRAWN
        finally:
            w.close()


class TestOperations:
    def _remeasure(self) -> Any:
        from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
        from dispute_agent.modules.reasoning.llm.reasoner import LlmReasoner
        from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry
        from tests.model_fixtures import PROMPTS, valid_answer

        clock, ids = FakeClock(NOW), FakeIdGenerator()
        answer = valid_answer(
            action="request_remeasurement",
            action_parameters={"remeasurement_question": "Please remeasure the parcel."},
        )
        reasoner = LlmReasoner(
            prompts=FilePromptRegistry(PROMPTS, allow_inactive=True),
            gateway=FakeModelGateway(script=[answer, answer]),
            ids=ids,
            clock=clock,
        )
        w = open_world(
            "scanner_fault", policy=PolicyConfig(), clock=clock, ids=ids, reasoner=reasoner
        )
        assert w.drive().case.state is S.AWAITING_OPS
        return w

    def test_waiting_for_ops_arms_the_ops_timeout(self) -> None:
        w = self._remeasure()
        try:
            assert TimerKind.OPS_TIMEOUT in active_timers(w)
        finally:
            w.close()

    def test_no_finding_in_120_hours_goes_to_a_person(self) -> None:
        w = self._remeasure()
        try:
            w.clock.advance(121 * HOURS)
            w.tick()
            case = w.report().case
            assert case.state is S.NEEDS_HUMAN_REVIEW
            assert case.review_reason.code is ReviewCode.OPS_TIMEOUT
        finally:
            w.close()

    def test_a_finding_reopens_the_evidence_round(self) -> None:
        """T31: routed by the issue key on the finding, never by anything it says."""
        w = self._remeasure()
        try:
            with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
                [issue] = [a for a in work.actions.for_case(w.case_id) if a.external_ref]
            finding = inbound(
                InboundEventType.OPS_FINDING_RECEIVED,
                "jira",
                f"{issue.external_ref}:f1",
                OpsFindingReceived(
                    finding=OpsFinding(
                        issue_key=str(issue.external_ref),
                        finding_id="f1",
                        kind="remeasured",
                        measured={"weight_grams": 900},
                        at=NOW,
                    )
                ),
            )
            result = w.runner.submit(finding)
            assert result.reaction == "transition:ops_finding_received"
            assert S.GATHERING_EVIDENCE in w.report().path[-6:]
        finally:
            w.close()


class TestRestart:
    def test_a_missed_reminder_runs_once_after_a_restart(self) -> None:
        """orchestrator.md section 7: drop everything in memory, recover, tick."""
        w = awaiting_seller()
        try:
            w.clock.advance(49 * HOURS)  # the process was down when the reminder fell due
            fresh = Runner(w.engine, w.line)
            fresh.recover()
            fresh.recover()  # recovery is idempotent
            fresh.tick()
            with SqliteUnitOfWork(w.engine, clock=w.clock) as work:
                kinds = [
                    kind.value
                    for a in work.approvals.for_case(w.case_id)
                    if (kind := a.request.message_kind) is not None
                ]
            assert kinds.count("reminder") == 1
        finally:
            w.close()


@pytest.mark.parametrize("state_after", [S.AWAITING_SELLER])
def test_every_moved_event_is_on_the_trail(state_after: CaseState) -> None:
    """I8: what dispatch did is in the events table, with its cause."""
    w = awaiting_seller()
    try:
        w.reply("photo attached")
        kinds = [e.type for e in events(w)]
        assert InboundEventType.SELLER_REPLY_RECEIVED in kinds
        assert kinds.count(InboundEventType.SELLER_REPLY_RECEIVED) == 1
    finally:
        w.close()


_ = SELLER_CONTACT
