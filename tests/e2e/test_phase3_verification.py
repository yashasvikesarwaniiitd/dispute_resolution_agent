"""Card P3-04: the executor driving real adapters, through the three cases that hurt.

Each of these is a moment where the system knows less than it would like, and the whole
point is that it says so rather than guessing.

**S12a — the send timed out but the message went.** The honest answer is not "retry" and not
"failed": it is to look the idempotency key up and discover the message is already in Sent
Items. One email, not two.

**S12b — the message is in Drafts and nowhere else.** Nobody can tell whether `/send` ran.
Re-sending might produce a second copy; calling it failed might abandon a message the seller
already has. The action becomes UNKNOWN and a person resolves it (I4).

**S12c — Jira refuses with a 400.** An argument we got wrong. Retrying gets the same answer
more slowly, so the action fails immediately and the case goes to a human.

Run against the fakes with fault tokens. The same cases run against the real services under
`make test-integration`, where most of them cannot be arranged on demand — which is exactly
why they are pinned here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.actions import Action, IssuePayload, MessagePayload
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    MessageKind,
)
from dispute_agent.kernel.domain.errors import TransientPortError
from dispute_agent.kernel.domain.external import IssueSpec, OutboundMessage
from dispute_agent.kernel.domain.ids import (
    ActionId,
    CaseId,
    IdempotencyKey,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.executor.executor import Outcome
from dispute_agent.modules.email.fake.channel import FakeMessageChannel, normalise
from dispute_agent.modules.email.write_port import MessageWritePort
from dispute_agent.modules.ticketing.fake.ticketing import FakeTicketingSystem
from dispute_agent.modules.ticketing.write_port import IssueWritePort

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 3, 1, 9, 5, tzinfo=UTC)
CASE = CaseId("case_p304")
SELLER = "seller@example.invalid"
KEY = IdempotencyKey("wd1:" + "1" * 64)
HASH = PayloadHash("sha256:" + "1" * 64)

BODY = "Your charge was ₹152.00.\r\n\r\n\r\nRegards   "
MESSAGE = OutboundMessage(
    to=SELLER,
    subject="About your weight dispute",
    body=BODY,
    case_id=CASE,
    kind=MessageKind.EVIDENCE_REQUEST,
)
SPEC = IssueSpec(
    project_key="WD",
    summary="Remeasure SHP-000001",
    description="Please remeasure.\n\nDeclared 20 x 15 x 10 cm.",
    labels=("dispute-agent",),
    case_id=CASE,
)


@dataclass
class Actions:
    """The smallest ActionStore the executor needs, so these tests stay about the executor."""

    rows: dict[str, Action] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)

    def insert(self, action: Action) -> Action:
        self.rows[str(action.id)] = action
        return action

    def get(self, action_id: ActionId) -> Action:
        return self.rows[str(action_id)]

    def claim(self, action_id: ActionId, worker: str, now: datetime, lease_s: int) -> bool:
        if str(action_id) in self.claimed:
            return False
        self.claimed.add(str(action_id))
        return True

    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: Any
    ) -> Action:
        current = self.rows[str(action_id)]
        updated = current.model_copy(update={"status": status, **fields})
        self.rows[str(action_id)] = updated
        return updated

    def find_approval_for(self, intent_id: object) -> None:
        return None

    def live_credits(self, shipment_ref: str, reason_code: str) -> int:
        return 0

    def has_verified_credit(self, case_id: object) -> bool:
        return False

    def outbound_message_count(self, case_id: object, since: datetime | None = None) -> int:
        return 0


class NoKillSwitch:
    def is_engaged(self) -> bool:
        return False


def executor(store: Actions) -> Any:
    from dispute_agent.kernel.executor.executor import ActionExecutor
    from dispute_agent.kernel.policy.engine import PolicyEngine
    from dispute_agent.kernel.policy.rules import phase_3_rules

    engine = PolicyEngine(phase_3_rules(), FakeClock(NOW), FakeIdGenerator())
    return ActionExecutor(engine, store, NoKillSwitch())


def planned(store: Actions, action_type: ActionType, payload: Any) -> ActionId:
    """A PLANNED row, as `plan` would have committed it.

    The row exists before any call is made, which is what lets recovery tell "we may have
    written" from "we never started" (executor.md section 4).
    """
    action_id = ActionId("act_p304")
    store.insert(
        Action(
            id=action_id,
            case_id=CASE,
            intent_id=IntentId("int_p304"),
            action_type=action_type,
            action_seq=1,
            idempotency_key=KEY,
            payload_hash=HASH,
            status=ActionStatus.PLANNED,
            planned_at=NOW,
        )
    )
    return action_id


def message_port(**faults: str) -> tuple[MessageWritePort, FakeMessageChannel]:
    channel = FakeMessageChannel(faults=dict(faults), now=NOW)
    return MessageWritePort(channel=channel), channel


def issue_port(**faults: str) -> tuple[IssueWritePort, FakeTicketingSystem]:
    system = FakeTicketingSystem(faults=dict(faults), now=NOW)
    return IssueWritePort(system=system), system


class TestAGoodSend:
    def test_it_goes_from_planned_to_sent_to_verified(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()

        sent = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert sent.outcome is Outcome.SENT
        assert store.get(action_id).status is ActionStatus.SENT

        channel.now = LATER
        receipt = channel.find_sent(KEY)
        verified = executor(store).verify(
            action_id,
            port,
            receipt,
            {"subject": MESSAGE.subject, "body": normalise(BODY), "in_sent_items": True},
            LATER,
        )
        assert verified.outcome is Outcome.VERIFIED
        assert store.get(action_id).status is ActionStatus.VERIFIED

    def test_exactly_one_message_exists(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, MESSAGE, NOW)
        assert channel.sent_count() == 1

    def test_a_message_not_yet_in_sent_items_rests_rather_than_failing(self) -> None:
        """The asynchronous move is normal, not an error. Failing here would mark a
        perfectly good send as broken a second after it happened."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, MESSAGE, NOW)

        receipt = channel.find_sent(KEY)
        result = executor(store).verify(action_id, port, receipt, {}, NOW)
        assert result.outcome is Outcome.BLOCKED
        assert result.reason == "not_visible_yet"
        assert store.get(action_id).status is ActionStatus.SENT


class TestS12aTheSendTimedOutButItWent:
    """One email, not two - driven through the executor this time.

    The first version of this class called `port.send` directly, caught the timeout itself,
    and only then asked the executor - so the executor's own handling of a timed-out send
    was never exercised. That is the path the Phase 3 review found broken: a lost answer
    was labelled "certainly did not happen", the action recorded FAILED, and a retry would
    have sent the seller a second copy.
    """

    def test_a_lost_answer_is_settled_by_lookup_and_recorded_sent(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="timeout")

        result = executor(store).execute(action_id, port, MESSAGE, NOW)

        assert result.outcome is Outcome.SENT
        assert result.reason == "found_after_ambiguous_write"
        assert store.get(action_id).status is ActionStatus.SENT
        assert channel.sent_count() == 1

    def test_it_is_never_recorded_failed(self) -> None:
        """FAILED frees the contact-limit slot and, for credits, the duplicate-credit slot -
        for something that may well have happened."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, _ = message_port(send="timeout")

        executor(store).execute(action_id, port, MESSAGE, NOW)
        assert store.get(action_id).status is not ActionStatus.FAILED

    def test_when_the_lookup_cannot_settle_it_a_person_does(self) -> None:
        """Answer lost, and the lookup itself fails: nobody can tell, so UNKNOWN (I4)."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="timeout", find_sent="transient")

        result = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert store.get(action_id).status is ActionStatus.UNKNOWN
        assert channel.sent_count() == 1

    def test_the_seller_receives_one_copy_however_often_it_is_re_run(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="timeout")

        for _ in range(3):
            store.claimed.clear()
            store.set_status(action_id, ActionStatus.PLANNED, NOW)
            executor(store).execute(action_id, port, MESSAGE, NOW)
        assert channel.sent_count() == 1

    def test_a_send_that_never_left_is_the_only_timeout_that_fails(self) -> None:
        """The control. TransientPortError - nothing was sent - is still FAILED and safe
        to retry; that is the one case where "failed" is the truth."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())

        class NeverConnects(MessageWritePort):
            def send(self, payload: Any, key: IdempotencyKey) -> Any:
                raise TransientPortError("could not connect")

        port = NeverConnects(channel=FakeMessageChannel(now=NOW))
        result = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert result.outcome is Outcome.FAILED


class TestS12bStuckInDrafts:
    """Nobody can tell whether it was sent, so nobody pretends to."""

    def test_the_action_becomes_unknown(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="stuck_in_drafts")

        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)
        assert channel.drafts()

        result = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert store.get(action_id).status is ActionStatus.UNKNOWN

    def test_it_is_never_treated_as_success(self) -> None:
        """I4. UNKNOWN is not failure either - it is the honest third answer."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, _ = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        result = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert result.is_success is False
        assert result.outcome is not Outcome.FAILED

    def test_the_draft_is_not_sent_by_the_retry(self) -> None:
        """integrations.md 1.3: never send a draft that carries an idempotency key."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        executor(store).execute(action_id, port, MESSAGE, NOW)
        assert channel.sent_count() == 0

    def test_the_reason_carries_the_draft_for_a_person_to_find(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, _ = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        result = executor(store).execute(action_id, port, MESSAGE, NOW)
        assert "ambiguous" in (result.reason or "")


class TestS12cJiraRefuses:
    def test_a_400_fails_immediately_without_retrying(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port(upsert_issue="permanent")

        result = executor(store).execute(action_id, port, SPEC, NOW)
        assert result.outcome is Outcome.FAILED
        assert store.get(action_id).status is ActionStatus.FAILED
        assert system.issue_count() == 0

    def test_an_issue_created_but_unmarked_is_unknown_not_failed(self) -> None:
        """The issue exists. Calling it failed would leave a real ticket nobody tracks."""
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port(upsert_issue="marker_fails")

        result = executor(store).execute(action_id, port, SPEC, NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert system.issue_count() == 1


class TestVerificationComparesWhatWasMeant:
    def test_a_changed_body_is_never_verified(self) -> None:
        """I2 and I13 together: what is out there is not what was approved."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, MESSAGE, NOW)
        channel.now = LATER

        result = executor(store).verify(
            action_id,
            port,
            channel.find_sent(KEY),
            {"body": "something else entirely"},
            LATER,
        )
        assert result.outcome is Outcome.UNKNOWN
        assert "verification_mismatch" in (result.reason or "")

    def test_a_body_that_only_differs_in_whitespace_still_verifies(self) -> None:
        """No mail system promises a byte-for-byte round trip. Reporting correct sends as
        unverified teaches whoever reads the queue to ignore it."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, MESSAGE, NOW)
        channel.now = LATER

        result = executor(store).verify(
            action_id, port, channel.find_sent(KEY), {"body": normalise(BODY)}, LATER
        )
        assert result.outcome is Outcome.VERIFIED

    def test_an_issue_description_is_compared_as_text(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port()
        sent = executor(store).execute(action_id, port, SPEC, NOW)
        assert sent.outcome is Outcome.SENT

        from dispute_agent.modules.ticketing.jira_cloud.ticketing import (
            normalise as jira_normalise,
        )

        ref = system.find_issue(KEY) or system._issues[0].ref
        result = executor(store).verify(
            action_id,
            port,
            ref,
            {
                "summary": SPEC.summary,
                "description_text": jira_normalise(SPEC.description),
            },
            NOW,
        )
        assert result.outcome is Outcome.VERIFIED


class TestTheBridgesTellTheTruth:
    def test_the_email_bridge_reports_the_channel_s_capability(self) -> None:
        """Answering on the adapter's behalf would be the worst possible place to be wrong:
        the executor reads this to decide whether a retry could send twice."""
        port, channel = message_port()
        assert port.idempotent_writes is channel.idempotent_writes
        assert port.idempotent_writes is False

    def test_the_jira_bridge_reports_the_system_s_capability(self) -> None:
        port, system = issue_port()
        assert port.idempotent_writes is system.idempotent_writes
        assert port.idempotent_writes is False


def _message_payload() -> MessagePayload:
    return MessagePayload(
        kind=MessageKind.EVIDENCE_REQUEST, to=SELLER, subject=MESSAGE.subject, body=BODY
    )


def _issue_payload() -> IssuePayload:
    return IssuePayload(summary=SPEC.summary, description=SPEC.description)
