"""The Gmail adapter against a stand-in Gmail server (ADR 0038). No network, no account.

The stand-in follows the documented API shapes (users.messages.send/list/get) and can be told
to misbehave in the ways the real one might: drop our custom header, rewrite our Message-ID,
lose an answer, rate-limit, refuse a token.
"""

from __future__ import annotations

import base64
import email
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import policy
from typing import Any

import httpx
import pytest

from dispute_agent.kernel.domain.enums import MessageKind
from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import OutboundMessage, SendReceipt
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.email.fake.channel import normalise
from dispute_agent.modules.email.gmail_api.auth import REAUTH_REQUIRED, GmailAuth
from dispute_agent.modules.email.gmail_api.channel import (
    IDEMPOTENCY_HEADER,
    GmailChannel,
    message_id_for,
)

MAILBOX = "pilot@gmail.example"
SELLER = "seller@example.invalid"
KEY = IdempotencyKey("wd1:" + "a" * 64)
OTHER = IdempotencyKey("wd1:" + "b" * 64)
BODY = "Dear seller,   \r\n\r\n\r\nYour charge is ₹152.00.\r\n\r\nRegards   "
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def outbound(body: str = BODY) -> OutboundMessage:
    return OutboundMessage(
        to=SELLER,
        subject="About your weight dispute CLM-000001",
        body=body,
        case_id=CaseId("case_gmail"),
        kind=MessageKind.EVIDENCE_REQUEST,
    )


@dataclass
class StubAuth:
    def token(self) -> str:
        return "access-token-value"


@dataclass
class Gmail:
    """A small in-memory Gmail. `faults` maps an operation to a misbehaviour."""

    faults: dict[str, str] = field(default_factory=dict)
    messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    sends: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/gmail/v1/users/me")
        if request.method == "POST" and path == "/messages/send":
            return self._send(request)
        if request.method == "GET" and path == "/messages":
            return self._list(request)
        if request.method == "GET" and path.startswith("/messages/"):
            return self._get(path.rsplit("/", 1)[1], request)
        return httpx.Response(404, json={"error": {"status": "NOT_FOUND"}})

    def _send(self, request: httpx.Request) -> httpx.Response:
        fault = self.faults.get("send")
        if fault == "connect":
            raise httpx.ConnectError("no route")
        if fault == "rate":
            return _error(403, "rateLimitExceeded")
        if fault == "auth":
            return _error(401, "authError")
        if fault == "scope":
            return _error(403, "insufficientPermissions")
        if fault == "server":
            return httpx.Response(500)
        if fault == "bad":
            return _error(400, "invalidArgument")
        raw = json.loads(request.content)["raw"]
        parsed = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
        self.sends += 1
        number = len(self.messages) + 1
        headers = [{"name": k, "value": str(v)} for k, v in parsed.items()]
        if self.faults.get("store") == "drop_header":
            headers = [h for h in headers if h["name"] != IDEMPOTENCY_HEADER]
        if self.faults.get("store") == "rewrite_message_id":
            headers = [
                {"name": "Message-ID", "value": f"<gmail-{number}@mail.gmail.com>"}
                if h["name"] == "Message-ID"
                else h
                for h in headers
            ]
        body = parsed.get_content()
        self.messages[f"m{number}"] = {
            "id": f"m{number}",
            "threadId": f"t{number}",
            "labelIds": [] if self.faults.get("store") == "not_yet_sent" else ["SENT"],
            "internalDate": str(int(NOW.timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": headers,
                "body": {"data": base64.urlsafe_b64encode(body.encode("utf-8")).decode()},
            },
        }
        if fault == "timeout":
            raise httpx.ReadTimeout("the answer was lost")
        return httpx.Response(200, json={"id": f"m{number}", "threadId": f"t{number}"})

    def _list(self, request: httpx.Request) -> httpx.Response:
        q = request.url.params.get("q", "")
        found = []
        for m in self.messages.values():
            heads = {h["name"].lower(): h["value"] for h in m["payload"]["headers"]}
            if "rfc822msgid:" in q:
                wanted = q.split("rfc822msgid:", 1)[1].strip()
                if heads.get("message-id", "").strip("<>") == wanted and "SENT" in m["labelIds"]:
                    found.append(m)
            elif q.startswith("in:sent"):
                if "SENT" in m["labelIds"]:
                    found.append(m)
            elif q.startswith("in:inbox"):
                after = int(q.split("after:", 1)[1])
                if "INBOX" in m["labelIds"] and int(m["internalDate"]) // 1000 > after:
                    found.append(m)
        return httpx.Response(
            200, json={"messages": [{"id": m["id"], "threadId": m["threadId"]} for m in found]}
        )

    def _get(self, message_id: str, request: httpx.Request) -> httpx.Response:
        stored = self.messages.get(message_id)
        if stored is None:
            return _error(404, "notFound")
        if request.url.params.get("format") == "metadata":
            wanted = {v.lower() for v in request.url.params.get_list("metadataHeaders")}
            heads = [h for h in stored["payload"]["headers"] if h["name"].lower() in wanted]
            return httpx.Response(200, json={**stored, "payload": {"headers": heads}})
        return httpx.Response(200, json=stored)

    def deliver(self, sender: str, subject: str, body: str, at: datetime, mid: str) -> None:
        number = len(self.messages) + 1
        self.messages[f"in{number}"] = {
            "id": f"in{number}",
            "threadId": f"t-in{number}",
            "labelIds": ["INBOX"],
            "internalDate": str(int(at.timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": sender},
                    {"name": "To", "value": MAILBOX},
                    {"name": "Subject", "value": subject},
                    {"name": "Message-ID", "value": mid},
                ],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
            },
        }

    def channel(self) -> GmailChannel:
        client = httpx.Client(
            base_url="https://gmail.googleapis.com/gmail/v1/users/me",
            transport=httpx.MockTransport(self.handler),
        )
        return GmailChannel(auth=StubAuth(), mailbox=MAILBOX, client=client)  # type: ignore[arg-type]


def _error(status: int, reason: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "errors": [{"reason": reason}]}})


class TestSending:
    def test_the_message_carries_both_markers_and_plain_text(self) -> None:
        gmail = Gmail()
        receipt = gmail.channel().send(outbound(), KEY)
        stored = gmail.messages[receipt.external_message_id]
        heads = {h["name"]: h["value"] for h in stored["payload"]["headers"]}
        assert heads[IDEMPOTENCY_HEADER] == KEY
        assert heads["Message-ID"] == message_id_for(KEY)
        assert heads["To"] == SELLER and heads["From"] == MAILBOX
        assert stored["payload"]["mimeType"] == "text/plain"
        assert receipt.conversation_id == "t1"

    def test_writes_are_declared_not_idempotent(self) -> None:
        channel = Gmail().channel()
        assert channel.idempotent_writes is False and channel.lookup_supported is True

    @pytest.mark.parametrize(
        ("fault", "error"),
        [
            ("timeout", AmbiguousOutcome),
            ("server", AmbiguousOutcome),
            ("connect", TransientPortError),
            ("rate", TransientPortError),
            ("auth", AuthPortError),
            ("scope", AuthPortError),
            ("bad", PermanentPortError),
        ],
    )
    def test_each_failure_maps_to_what_the_executor_acts_on(self, fault: str, error: type) -> None:
        with pytest.raises(error):
            Gmail(faults={"send": fault}).channel().send(outbound(), KEY)

    def test_an_error_never_echoes_the_bearer_token(self) -> None:
        with pytest.raises(AuthPortError) as refused:
            Gmail(faults={"send": "auth"}).channel().send(outbound(), KEY)
        assert "access-token-value" not in str(refused.value)


class TestLookup:
    def test_found_by_message_id(self) -> None:
        gmail = Gmail()
        sent = gmail.channel().send(outbound(), KEY)
        found = gmail.channel().find_sent(KEY)
        assert found is not None and found.external_message_id == sent.external_message_id

    def test_found_by_header_when_gmail_rewrites_the_message_id(self) -> None:
        gmail = Gmail(faults={"store": "rewrite_message_id"})
        sent = gmail.channel().send(outbound(), KEY)
        found = gmail.channel().find_sent(KEY)
        assert found is not None and found.external_message_id == sent.external_message_id

    def test_found_by_message_id_when_gmail_drops_the_header(self) -> None:
        gmail = Gmail(faults={"store": "drop_header"})
        sent = gmail.channel().send(outbound(), KEY)
        found = gmail.channel().find_sent(KEY)
        assert found is not None and found.external_message_id == sent.external_message_id

    def test_a_lost_answer_that_really_sent_is_findable(self) -> None:
        gmail = Gmail(faults={"send": "timeout"})
        with pytest.raises(AmbiguousOutcome):
            gmail.channel().send(outbound(), KEY)
        gmail.faults.clear()
        assert gmail.channel().find_sent(KEY) is not None
        assert gmail.sends == 1

    def test_an_unused_key_is_not_found(self) -> None:
        gmail = Gmail()
        gmail.channel().send(outbound(), KEY)
        assert gmail.channel().find_sent(OTHER) is None


class TestReadBack:
    def test_the_text_sent_is_the_text_read_back(self) -> None:
        gmail = Gmail()
        receipt = gmail.channel().send(outbound(), KEY)
        snapshot = gmail.channel().get_message(receipt)
        assert snapshot.subject == outbound().subject
        assert snapshot.to == (SELLER,)
        assert snapshot.idempotency_key == KEY
        assert snapshot.in_sent_items is True
        assert normalise(snapshot.body) == normalise(BODY)

    def test_the_key_is_read_from_the_message_id_when_the_header_is_gone(self) -> None:
        gmail = Gmail(faults={"store": "drop_header"})
        receipt = gmail.channel().send(outbound(), KEY)
        assert gmail.channel().get_message(receipt).idempotency_key == KEY

    def test_without_the_sent_label_it_is_not_yet_visible(self) -> None:
        gmail = Gmail(faults={"store": "not_yet_sent"})
        receipt = gmail.channel().send(outbound(), KEY)
        with pytest.raises(ExternalNotFound):
            gmail.channel().get_message(receipt)

    def test_a_message_that_never_existed_is_not_found(self) -> None:
        with pytest.raises(ExternalNotFound):
            Gmail().channel().get_message(SendReceipt(external_message_id="nope", sent_at=NOW))


class TestReplies:
    def test_replies_arrive_as_untrusted_text_and_include_the_boundary(self) -> None:
        gmail = Gmail()
        gmail.deliver(SELLER, "Re: your dispute", "Here is the photo.", NOW, "<r1@example.invalid>")
        gmail.deliver(SELLER, "Old", "older", NOW - timedelta(minutes=5), "<r0@example.invalid>")
        [reply] = gmail.channel().fetch_replies(NOW)
        assert isinstance(reply.subject, UntrustedText) and isinstance(reply.body, UntrustedText)
        assert reply.from_addr == SELLER and reply.received_at == NOW
        assert reply.internet_message_id == "<r1@example.invalid>"

    def test_a_bounce_is_routed_by_our_own_key(self) -> None:
        gmail = Gmail()
        sent = gmail.channel().send(outbound(), KEY)
        returned = f"Delivery failed.\nMessage-ID: {message_id_for(KEY)}\n"
        gmail.deliver("mailer-daemon@googlemail.com", "Delivery Status", returned, NOW, "<b1@x>")
        [bounce] = gmail.channel().fetch_replies(NOW)
        assert bounce.is_delivery_failure is True
        assert bounce.failed_message_id == sent.external_message_id


@dataclass
class MemoryStore:
    value: str | None = None

    def load(self) -> str:
        if self.value is None:
            raise FileNotFoundError
        return self.value

    def save(self, value: str) -> None:
        self.value = value


def token_endpoint(answer: httpx.Response, seen: list[dict[str, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qsl

        seen.append(dict(parse_qsl(request.content.decode())))
        return answer

    return httpx.Client(transport=httpx.MockTransport(handler))


class TestAuth:
    def auth(
        self, store: MemoryStore, answer: httpx.Response, seen: list[dict[str, str]]
    ) -> GmailAuth:
        return GmailAuth(
            client_id="client-id",
            client_secret="client-secret-value",
            store=store,
            client=token_endpoint(answer, seen),
        )

    def test_a_stored_refresh_token_buys_an_access_token_once(self) -> None:
        seen: list[dict[str, str]] = []
        ok = httpx.Response(200, json={"access_token": "ya29.fresh", "expires_in": 3600})
        auth = self.auth(MemoryStore(json.dumps({"refresh_token": "1//r"})), ok, seen)
        assert auth.token() == "ya29.fresh"
        assert auth.token() == "ya29.fresh"
        assert len(seen) == 1 and seen[0]["grant_type"] == "refresh_token"

    def test_not_signed_in_is_reauth_required(self) -> None:
        auth = self.auth(MemoryStore(), httpx.Response(200, json={}), [])
        with pytest.raises(AuthPortError, match=REAUTH_REQUIRED):
            auth.token()
        assert auth.signed_in() is False

    def test_an_expired_refresh_token_is_auth_not_transient(self) -> None:
        """Testing-status apps lose the refresh token after 7 days: Google says invalid_grant."""
        gone = httpx.Response(400, json={"error": "invalid_grant"})
        auth = self.auth(MemoryStore(json.dumps({"refresh_token": "1//old"})), gone, [])
        with pytest.raises(AuthPortError, match=REAUTH_REQUIRED) as refused:
            auth.token()
        assert "1//old" not in str(refused.value) and "client-secret" not in str(refused.value)

    def test_a_google_outage_is_transient(self) -> None:
        down = httpx.Response(503)
        auth = self.auth(MemoryStore(json.dumps({"refresh_token": "1//r"})), down, [])
        with pytest.raises(TransientPortError):
            auth.token()

    def test_the_browser_login_uses_pkce_and_stores_only_the_refresh_token(self) -> None:
        seen: list[dict[str, str]] = []
        granted = httpx.Response(
            200, json={"access_token": "ya29.a", "refresh_token": "1//new", "expires_in": 3600}
        )
        store = MemoryStore()
        auth = self.auth(store, granted, seen)

        def browser(url: str) -> None:
            from threading import Thread
            from urllib.parse import parse_qs, urlparse
            from urllib.request import urlopen

            query = parse_qs(urlparse(url).query)
            assert query["code_challenge_method"] == ["S256"]
            assert "gmail.send" in query["scope"][0] and "gmail.readonly" in query["scope"][0]
            target = f"{query['redirect_uri'][0]}/?code=abc&state={query['state'][0]}"
            Thread(target=lambda: urlopen(target, timeout=5).read(), daemon=True).start()

        auth.browser_login(open_browser=browser, wait_s=10)
        assert json.loads(store.value or "{}") == {"refresh_token": "1//new"}
        assert seen[0]["grant_type"] == "authorization_code" and seen[0]["code"] == "abc"
        assert len(seen[0]["code_verifier"]) >= 43
