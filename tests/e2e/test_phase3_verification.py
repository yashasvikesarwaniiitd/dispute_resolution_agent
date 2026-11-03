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
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import (
    ActionId,
    CaseId,
    IdempotencyKey,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.executor.executor import Outcome
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
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
        # The contract executor.md 5.1 rests on: a successful claim counts the attempt.
        current = self.rows[str(action_id)]
        self.rows[str(action_id)] = current.model_copy(update={"attempts": current.attempts + 1})
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
            # The real hash of the approved plan. execute refuses anything else.
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            status=ActionStatus.PLANNED,
            planned_at=NOW,
        )
    )
    return action_id


def message_port(**faults: str) -> tuple[MessageWritePort, FakeMessageChannel]:
    channel = FakeMessageChannel(faults=dict(faults), now=NOW)
    return MessageWritePort(channel=channel, case_id=CASE), channel


def issue_port(**faults: str) -> tuple[IssueWritePort, FakeTicketingSystem]:
    system = FakeTicketingSystem(faults=dict(faults), now=NOW)
    return IssueWritePort(system=system, case_id=CASE, project_key="WD"), system


class TestAGoodSend:
    def test_it_goes_from_planned_to_sent_to_verified(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()

        sent = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert sent.outcome is Outcome.SENT
        assert store.get(action_id).status is ActionStatus.SENT

        channel.now = LATER
        receipt = channel.find_sent(KEY)
        verified = executor(store).verify(action_id, port, receipt, _message_payload(), LATER)
        assert verified.outcome is Outcome.VERIFIED
        assert store.get(action_id).status is ActionStatus.VERIFIED

    def test_exactly_one_message_exists(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, _message_payload(), NOW)
        assert channel.sent_count() == 1

    def test_a_message_not_yet_in_sent_items_rests_rather_than_failing(self) -> None:
        """The asynchronous move is normal, not an error. Failing here would mark a
        perfectly good send as broken a second after it happened."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, _message_payload(), NOW)

        receipt = channel.find_sent(KEY)
        result = executor(store).verify(action_id, port, receipt, _message_payload(), NOW)
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

        result = executor(store).execute(action_id, port, _message_payload(), NOW)

        assert result.outcome is Outcome.SENT
        assert result.reason == "found_after_ambiguous_write"
        assert store.get(action_id).status is ActionStatus.SENT
        assert channel.sent_count() == 1

    def test_when_the_lookup_cannot_settle_it_a_person_does(self) -> None:
        """Answer lost, and the lookup itself fails: nobody can tell, so UNKNOWN (I4)."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="timeout", find_sent="transient")

        result = executor(store).execute(action_id, port, _message_payload(), NOW)
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
            executor(store).execute(action_id, port, _message_payload(), NOW)
        assert channel.sent_count() == 1

    def test_a_send_that_never_left_is_the_only_timeout_that_fails(self) -> None:
        """The control. TransientPortError - nothing was sent - is still FAILED and safe
        to retry; that is the one case where "failed" is the truth."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())

        class NeverConnects(MessageWritePort):
            def send(self, payload: Any, key: IdempotencyKey) -> Any:
                raise TransientPortError("could not connect")

        port = NeverConnects(channel=FakeMessageChannel(now=NOW), case_id=CASE)
        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert result.outcome is Outcome.FAILED


class TestTheSecondReviewsFindings:
    """Driven through the executor and the real Outlook adapter against a simulated Graph,
    so the HTTP status mapping and the executor's handling are tested together."""

    @staticmethod
    def graph_port(handler: Any) -> MessageWritePort:
        import httpx

        from dispute_agent.modules.email.outlook_graph.channel import GRAPH, OutlookGraphChannel
        from tests.unit.modules.email.test_outlook_graph import FakeAuth

        client = httpx.Client(base_url=GRAPH, transport=httpx.MockTransport(handler))
        return MessageWritePort(
            channel=OutlookGraphChannel(auth=FakeAuth(), client=client), case_id=CASE
        )

    def test_a_504_after_the_send_is_unknown_never_failed(self) -> None:
        """Finding 1. Graph accepted the send and then failed to answer."""
        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"value": []})
            if request.url.path.endswith("/send"):
                return httpx.Response(504)
            return httpx.Response(201, json={"id": "AAMk-1", "conversationId": "c"})

        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        result = executor(store).execute(
            action_id, self.graph_port(handler), _message_payload(), NOW
        )
        assert result.outcome is Outcome.UNKNOWN
        assert store.get(action_id).status is ActionStatus.UNKNOWN

    def test_a_lookup_that_hits_an_expired_login_still_settles_unknown(self) -> None:
        """Finding 2. The send timed out, then the Sent Items lookup got a 401. That used to
        escape and leave the action PLANNED, open to a second send."""
        import httpx

        gets = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                gets["n"] += 1
                # The two pre-write lookups are clean; the one after the send is refused.
                if gets["n"] <= 2:
                    return httpx.Response(200, json={"value": []})
                return httpx.Response(401)
            if request.url.path.endswith("/send"):
                raise httpx.ReadTimeout("answer lost")
            return httpx.Response(201, json={"id": "AAMk-1"})

        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        result = executor(store).execute(
            action_id, self.graph_port(handler), _message_payload(), NOW
        )
        assert result.outcome is Outcome.UNKNOWN
        assert store.get(action_id).status is ActionStatus.UNKNOWN

    def test_a_retry_that_cannot_find_the_earlier_attempt_does_not_send_again(self) -> None:
        """executor.md 5.1, the backstop. An earlier attempt may have written, the channel
        cannot recognise a repeat, and the lookup finds nothing: a person decides."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        store.rows[str(action_id)] = store.get(action_id).model_copy(update={"attempts": 1})
        port, channel = message_port()

        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert result.reason == "recovery_unconfirmed"
        assert channel.sent_count() == 0

    def test_a_first_attempt_is_still_sent(self) -> None:
        """The control: the rule must not stop an action that has never been tried."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert result.outcome is Outcome.SENT
        assert channel.sent_count() == 1


class TestS12bStuckInDrafts:
    """Nobody can tell whether it was sent, so nobody pretends to."""

    def test_the_action_becomes_unknown(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="stuck_in_drafts")

        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)
        assert channel.drafts()

        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert store.get(action_id).status is ActionStatus.UNKNOWN

    def test_it_is_never_treated_as_success(self) -> None:
        """I4. UNKNOWN is not failure either - it is the honest third answer."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, _ = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert result.is_success is False
        assert result.outcome is not Outcome.FAILED

    def test_the_draft_is_not_sent_by_the_retry(self) -> None:
        """integrations.md 1.3: never send a draft that carries an idempotency key."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        executor(store).execute(action_id, port, _message_payload(), NOW)
        assert channel.sent_count() == 0

    def test_the_reason_carries_the_draft_for_a_person_to_find(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, _ = message_port(send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            port.send(MESSAGE, KEY)

        result = executor(store).execute(action_id, port, _message_payload(), NOW)
        assert "ambiguous" in (result.reason or "")


class TestS12cJiraRefuses:
    def test_a_400_fails_immediately_without_retrying(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port(upsert_issue="permanent")

        result = executor(store).execute(action_id, port, _issue_payload(), NOW)
        assert result.outcome is Outcome.FAILED
        assert store.get(action_id).status is ActionStatus.FAILED
        assert system.issue_count() == 0

    def test_an_issue_created_but_unmarked_is_unknown_not_failed(self) -> None:
        """The issue exists. Calling it failed would leave a real ticket nobody tracks."""
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port(upsert_issue="marker_fails")

        result = executor(store).execute(action_id, port, _issue_payload(), NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert system.issue_count() == 1


class TestVerificationComparesWhatWasMeant:
    def test_a_changed_body_is_never_verified(self) -> None:
        """I2 and I13 together: what is out there is not what was approved.

        The far end is what changes here - the message in Sent Items is edited after the
        send - while verify is handed the honest approved plan. That is the real shape of
        the failure; the first version faked it by passing a wrong expectation instead.
        """
        from dispute_agent.modules.email.fake.channel import StoredMessage

        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, _message_payload(), NOW)
        channel.now = LATER

        sent = channel._all[0]
        channel._all[0] = StoredMessage(
            sent.receipt,
            sent.message.model_copy(update={"body": "something else entirely"}),
            sent.key,
            sent.folder,
            sent.created_at,
        )

        result = executor(store).verify(
            action_id, port, channel.find_sent(KEY), _message_payload(), LATER
        )
        assert result.outcome is Outcome.UNKNOWN
        assert "verification_mismatch" in (result.reason or "")

    def test_a_body_that_only_differs_in_whitespace_still_verifies(self) -> None:
        """No mail system promises a byte-for-byte round trip. Reporting correct sends as
        unverified teaches whoever reads the queue to ignore it."""
        store = Actions()
        action_id = planned(store, ActionType.SEND_MESSAGE, _message_payload())
        port, channel = message_port()
        executor(store).execute(action_id, port, _message_payload(), NOW)
        channel.now = LATER

        result = executor(store).verify(
            action_id, port, channel.find_sent(KEY), _message_payload(), LATER
        )
        assert result.outcome is Outcome.VERIFIED

    def test_an_issue_description_is_compared_as_text(self) -> None:
        store = Actions()
        action_id = planned(store, ActionType.UPSERT_ISSUE, _issue_payload())
        port, system = issue_port()
        sent = executor(store).execute(action_id, port, _issue_payload(), NOW)
        assert sent.outcome is Outcome.SENT

        ref = system.find_issue(KEY) or system._issues[0].ref
        result = executor(store).verify(action_id, port, ref, _issue_payload(), NOW)
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
