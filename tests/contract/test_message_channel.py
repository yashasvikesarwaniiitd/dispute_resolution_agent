"""Contract: MessageChannel (kernel-interfaces.md 6.4, integrations.md 1).

The cases below are the ones the executor's guarantees rest on. Two are worth naming now,
because they are what a fake usually gets wrong by being too kind:

`find_sent` must raise AmbiguousOutcome when the evidence cannot settle the question — a
marked message found only in Drafts is not proof that anything was sent (integrations.md
1.4). A fake that cheerfully returns None there would make every "did it send?" test pass
and leave I4 untested.

`send` with a reused idempotency key must not send twice, and the adapter has to say so
through `idempotent_writes` rather than by hoping. The executor reads that flag to decide
whether a retry is safe.

Every assertion here goes through the port. Conditions that no port method can create — a
message stranded in Drafts, the clock moving past a visibility delay — are arranged by the
adapter's own harness (`harness.py`), which is what keeps this suite free of anything only a
fake could satisfy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from dispute_agent.kernel.domain.enums import MessageKind
from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import InboundMessage, OutboundMessage
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
from tests.contract.conftest import build_or_skip, params, ready
from tests.contract.harness import UnsupportedError, arrange

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
SELLER = "seller@example.invalid"
CASE = CaseId("case_contract_channel")

KEY = IdempotencyKey("wd1:" + "a" * 64)
OTHER_KEY = IdempotencyKey("wd1:" + "b" * 64)

#: Deliberately awkward: trailing spaces, CRLF, a blank-line run and an accented character.
#: A body that survives this unchanged is one the executor can hash on both sides (I13).
BODY = "Dear seller,   \r\n\r\n\r\nYour charge is ₹152.00.\r\n\r\nRegards   "


def a_message(to: str = SELLER, body: str = BODY) -> OutboundMessage:
    return OutboundMessage(
        to=to,
        subject="About your weight dispute CLM-000001",
        body=body,
        case_id=CASE,
        kind=MessageKind.EVIDENCE_REQUEST,
    )


def a_reply(received_at: datetime = NOW, **overrides: Any) -> InboundMessage:
    fields: dict[str, Any] = {
        "external_message_id": "in-0001",
        "internet_message_id": "<reply@example.invalid>",
        "conversation_id": f"conv-{CASE}",
        "from_addr": SELLER,
        "to_addr": "agent@example.invalid",
        "subject": UntrustedText.create("Re: your dispute", "seller_email", "in-0001"),
        "body": UntrustedText.create("Here is the photo.", "seller_email", "in-0001"),
        "received_at": received_at,
    }
    fields.update(overrides)
    return InboundMessage(**fields)


class FakeChannelHarness:
    """Implements ChannelHarness for the in-memory fake."""

    name = "fake"

    def channel(self, **conditions: str) -> Any:
        return FakeMessageChannel(faults=dict(conditions), now=NOW)

    def deliver(self, channel: Any, message: InboundMessage) -> None:
        channel.deliver(message)

    def at(self, channel: Any, moment: datetime) -> None:
        channel.now = moment

    def sent_count(self, channel: Any) -> int:
        return cast("int", channel.sent_count())


class OutlookHarness:
    """The real adapter against the owner's pilot mailbox.

    Runs only under `make test-integration`, and only when the credentials exist. The three
    conditions it cannot arrange are honest refusals rather than quiet passes: there is no
    way to make a real mailbox time out on command, strand a draft, or move its clock, and
    a harness that faked any of them would be asserting something about a fake while
    claiming to test the real thing.
    """

    name = "outlook_graph"

    def channel(self, **conditions: str) -> Any:
        if conditions:
            raise UnsupportedError(
                f"a real mailbox cannot be told to {sorted(conditions.values())}"
            )
        from dispute_agent.infra.settings import Settings
        from dispute_agent.modules.email.outlook_graph.auth import GraphAuth
        from dispute_agent.modules.email.outlook_graph.channel import OutlookGraphChannel

        settings = Settings()
        if not settings.graph_client_id or not settings.test_seller_email:
            raise UnsupportedError(
                "GRAPH_CLIENT_ID and TEST_SELLER_EMAIL are not set; see docs/SETUP_CHANNELS.md"
            )
        auth = GraphAuth(
            client_id=settings.graph_client_id,
            authority=settings.graph_authority,
            cache_path=settings.graph_token_cache_path,
        )
        if not auth.signed_in():
            raise UnsupportedError("not signed in; run `make email-login`")
        return OutlookGraphChannel(auth=auth, timeout_s=settings.http_timeout_s)

    def deliver(self, channel: Any, message: InboundMessage) -> None:
        raise UnsupportedError("a reply has to be sent by a person, not injected")

    def at(self, channel: Any, moment: datetime) -> None:
        raise UnsupportedError("a real mailbox has its own clock")

    def sent_count(self, channel: Any) -> int:
        raise UnsupportedError("counting Sent Items would depend on the mailbox history")


class GmailHarness(OutlookHarness):
    """The Gmail adapter against the owner's pilot Gmail (ADR 0038). Same honest refusals."""

    name = "gmail_api"

    def channel(self, **conditions: str) -> Any:
        if conditions:
            raise UnsupportedError(
                f"a real mailbox cannot be told to {sorted(conditions.values())}"
            )
        from dispute_agent.infra.settings import Settings
        from dispute_agent.modules.email.gmail_api.auth import GmailAuth
        from dispute_agent.modules.email.gmail_api.channel import GmailChannel

        settings = Settings()
        secret = settings.gmail_client_secret
        if not (settings.gmail_client_id and secret and settings.gmail_mailbox):
            raise UnsupportedError("GMAIL_* settings are not set; see docs/SETUP_CHANNELS.md")
        if not settings.test_seller_email:
            raise UnsupportedError("TEST_SELLER_EMAIL is not set")
        auth = GmailAuth(
            client_id=settings.gmail_client_id,
            client_secret=secret.get_secret_value(),
            cache_path=settings.gmail_token_cache_path,
            timeout_s=settings.http_timeout_s,
        )
        if not auth.signed_in():
            raise UnsupportedError("not signed in; run `make gmail-login`")
        return GmailChannel(
            auth=auth, mailbox=settings.gmail_mailbox, timeout_s=settings.http_timeout_s
        )


ADAPTERS = [
    ready("fake", FakeChannelHarness),
    ready("outlook_graph", OutlookHarness, integration=True),
    ready("gmail_api", GmailHarness, integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def harness(request: pytest.FixtureRequest) -> Any:
    return build_or_skip(request.param)


@pytest.fixture
def channel(harness: Any) -> Any:
    return arrange(harness.channel)


class TestCapabilities:
    def test_it_declares_whether_writes_are_idempotent(self, channel: Any) -> None:
        assert isinstance(channel.idempotent_writes, bool)
        assert isinstance(channel.lookup_supported, bool)

    def test_the_idempotency_claim_is_earned_not_asserted(self, harness: Any, channel: Any) -> None:
        """The flag decides whether the executor may retry a send at all, so a wrong answer
        here is a second email to a seller.

        Either value is fine. Claiming True and producing two messages is not, and neither
        is claiming False while silently deduplicating - that would make the executor's
        lookup-before-write look unnecessary.
        """
        channel.send(a_message(), KEY)
        channel.send(a_message(), KEY)
        count = arrange(harness.sent_count, channel)
        if channel.idempotent_writes:
            assert count == 1, "claims idempotent writes but sent twice"
        else:
            assert count == 2, "says writes are not idempotent, so a repeat must show"


class TestSending:
    def test_a_send_returns_a_receipt_naming_the_message(self, channel: Any) -> None:
        receipt = channel.send(a_message(), KEY)
        assert receipt.external_message_id
        assert receipt.sent_at.tzinfo is not None
        assert receipt.duplicate is False

    def test_the_same_key_twice_sends_once(self, harness: Any, channel: Any) -> None:
        """The duplicate-side-effect gate, counted at the far end rather than read off a
        receipt. Only meaningful for an adapter that claims idempotent writes."""
        if not channel.idempotent_writes:
            pytest.skip("this adapter does not claim idempotent writes")
        channel.send(a_message(), KEY)
        channel.send(a_message(), KEY)
        assert arrange(harness.sent_count, channel) == 1

    def test_the_second_send_reports_itself_as_a_duplicate(self, channel: Any) -> None:
        if not channel.idempotent_writes:
            pytest.skip("this adapter does not claim idempotent writes")
        channel.send(a_message(), KEY)
        assert channel.send(a_message(), KEY).duplicate is True

    def test_different_keys_are_different_messages(self, harness: Any, channel: Any) -> None:
        """The control. Without it, an adapter that silently dropped every send after the
        first would pass the two tests above."""
        channel.send(a_message(), KEY)
        channel.send(a_message(), OTHER_KEY)
        assert arrange(harness.sent_count, channel) == 2

    def test_a_lost_answer_to_a_send_is_ambiguous_not_transient(self, harness: Any) -> None:
        """The distinction the executor acts on. TransientPortError promises "certainly
        did not happen" and is safe to retry. A send whose answer was lost may well have
        happened, so it must be AmbiguousOutcome - the Phase 3 review found both real
        adapters, and this fake, calling it transient, which recorded a sent email as
        FAILED and let a retry send a second copy."""
        channel = arrange(harness.channel, send="timeout")
        with pytest.raises(AmbiguousOutcome):
            channel.send(a_message(), KEY)

    def test_a_refusal_raises_permanent_and_is_not_a_timeout(self, harness: Any) -> None:
        channel = arrange(harness.channel, send="permanent")
        with pytest.raises(PermanentPortError) as refused:
            channel.send(a_message(), KEY)
        assert not isinstance(refused.value, TransientPortError)

    def test_bad_credentials_raise_auth_not_transient(self, harness: Any) -> None:
        """Retrying a credential failure is how a case spins for an hour before anyone
        finds out the token expired."""
        channel = arrange(harness.channel, send="auth")
        with pytest.raises(AuthPortError):
            channel.send(a_message(), KEY)


class TestLookup:
    def test_find_sent_returns_the_earlier_receipt(self, channel: Any) -> None:
        sent = channel.send(a_message(), KEY)
        found = channel.find_sent(KEY)
        assert found is not None
        assert found.external_message_id == sent.external_message_id

    def test_find_sent_returns_none_for_an_unused_key(self, channel: Any) -> None:
        assert channel.find_sent(OTHER_KEY) is None

    def test_a_timeout_that_really_sent_is_findable(self, harness: Any) -> None:
        """Scenario S12a, and the reason `find_sent` exists at all: the message went, the
        answer was lost, and the executor must discover that rather than send again."""
        channel = arrange(harness.channel, send="timeout")
        with pytest.raises(AmbiguousOutcome):
            channel.send(a_message(), KEY)
        assert channel.find_sent(KEY) is not None
        assert arrange(harness.sent_count, channel) == 1

    def test_a_draft_only_match_raises_ambiguous_outcome(self, harness: Any) -> None:
        """integrations.md 1.4. The case that keeps I4 honest.

        A draft carrying our key proves the draft was created and proves nothing about
        whether it was sent. Returning None would read as "nothing happened" and invite a
        second send; returning a receipt would claim a send that may never have occurred.
        """
        channel = arrange(harness.channel, send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            channel.send(a_message(), KEY)
        with pytest.raises(AmbiguousOutcome) as ambiguous:
            channel.find_sent(KEY)
        assert ambiguous.value.external_ref, "a person needs the id to resolve it"

    def test_a_draft_only_match_is_not_counted_as_sent(self, harness: Any) -> None:
        channel = arrange(harness.channel, send="stuck_in_drafts")
        with pytest.raises(TransientPortError):
            channel.send(a_message(), KEY)
        assert arrange(harness.sent_count, channel) == 0


class TestReadBack:
    def test_get_message_returns_the_text_that_was_sent(self, harness: Any, channel: Any) -> None:
        """I13: the executor hashes this and compares it with what was approved.

        Compared after normalisation, because no mail system promises a byte-for-byte round
        trip of a body (integrations.md 1.4). What must survive exactly is the subject, the
        recipient and the key.
        """
        from dispute_agent.modules.email.fake.channel import normalise

        receipt = channel.send(a_message(), KEY)
        arrange(harness.at, channel, NOW + timedelta(minutes=5))
        snapshot = channel.get_message(receipt)

        assert snapshot.subject == a_message().subject
        assert snapshot.to == (SELLER,)
        assert snapshot.idempotency_key == KEY
        assert normalise(snapshot.body) == normalise(BODY)

    def test_the_snapshot_says_it_is_in_sent_items(self, harness: Any, channel: Any) -> None:
        """ "Sent" means "in Sent Items", not "accepted by the API" (ADR 0010). Nothing here
        may claim delivery - that is a fact nobody at this end holds."""
        receipt = channel.send(a_message(), KEY)
        arrange(harness.at, channel, NOW + timedelta(minutes=5))
        assert channel.get_message(receipt).in_sent_items is True

    def test_get_message_raises_external_not_found_before_it_is_visible(
        self, harness: Any, channel: Any
    ) -> None:
        """The move out of Drafts is asynchronous. Raising keeps the case waiting; an empty
        snapshot would read as "sent but wrong" and fail a verification that is simply early."""
        receipt = channel.send(a_message(), KEY)
        with pytest.raises(ExternalNotFound):
            channel.get_message(receipt)

    def test_a_message_that_never_existed_raises_external_not_found(self, channel: Any) -> None:
        from dispute_agent.kernel.domain.external import SendReceipt

        phantom = SendReceipt(external_message_id="never-sent", sent_at=NOW)
        with pytest.raises(ExternalNotFound):
            channel.get_message(phantom)


class TestPolling:
    def test_fetch_replies_returns_untrusted_text(self, harness: Any, channel: Any) -> None:
        """I5: subject and body arrive labelled, from the adapter outwards. A reply that
        entered as a plain string would have to be labelled by whoever remembered to."""
        arrange(harness.deliver, channel, a_reply(received_at=NOW + timedelta(minutes=1)))
        replies = channel.fetch_replies(NOW)
        assert len(replies) == 1
        assert isinstance(replies[0].subject, UntrustedText)
        assert isinstance(replies[0].body, UntrustedText)

    def test_fetch_replies_includes_the_since_boundary(self, harness: Any, channel: Any) -> None:
        """Inclusive, so a message arriving in the same instant as the last one seen is not
        lost. Duplicates are cheap - the inbox deduplicates - and a missed reply is not."""
        arrange(harness.deliver, channel, a_reply(received_at=NOW))
        assert len(channel.fetch_replies(NOW)) == 1

    def test_fetch_replies_excludes_anything_older(self, harness: Any, channel: Any) -> None:
        arrange(harness.deliver, channel, a_reply(received_at=NOW - timedelta(minutes=1)))
        assert channel.fetch_replies(NOW) == ()

    def test_a_delivery_failure_names_the_message_that_failed(
        self, harness: Any, channel: Any
    ) -> None:
        """Scenario S45. A bounce that cannot be routed to its case leaves the seller timers
        running down on a message that never arrived."""
        bounce = a_reply(
            received_at=NOW + timedelta(minutes=1),
            external_message_id="in-bounce",
            is_delivery_failure=True,
            failed_message_id="msg-0001",
        )
        arrange(harness.deliver, channel, bounce)
        replies = channel.fetch_replies(NOW)
        assert replies[0].is_delivery_failure is True
        assert replies[0].failed_message_id == "msg-0001"

    def test_a_transient_poll_failure_is_transient(self, harness: Any) -> None:
        channel = arrange(harness.channel, fetch_replies="transient")
        with pytest.raises(TransientPortError):
            channel.fetch_replies(NOW)
