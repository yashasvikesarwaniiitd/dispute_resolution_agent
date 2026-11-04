"""Gmail through the Gmail API (ADR 0038; developers.google.com/gmail/api).

The same contract as the Outlook adapter (`outlook_graph/channel.py`), with Gmail's shape:

**Sending.** One call, `users.messages.send` with the RFC 2822 message base64url-encoded in
`raw`. There is no draft step, so there is no "stuck in Drafts" state to report: a send either
happened, did not happen, or its answer was lost (AmbiguousOutcome).

**The marker - two of them.** Google's documentation does not promise that a custom header
survives on the stored copy, nor that a client-supplied Message-ID is kept. So every message
carries both: the header `X-Dispute-Idempotency-Key: <key>` and the Message-ID
`<wd1-<hex>@dispute-agent.local>`, both derived from the executor's key. `find_sent` searches
Sent by `rfc822msgid:` first, then scans the last week of Sent for the header. The live test
records which one Gmail keeps (ADR 0038).

**Read-back.** `users.messages.get?format=full`; only a message carrying the `SENT` label
counts, and the body compared is the text/plain part, decoded from base64url. "Verified"
means "in Sent with the approved text", never "delivered" (ADR 0010).

**Errors.** As the Outlook adapter: a connection never made is transient; a write whose answer
was lost is ambiguous; 401 is an auth error; Gmail's rate limits arrive as 429 *or* as 403 with
reason `rateLimitExceeded`/`userRateLimitExceeded` (Google's handle-errors guide), and both are
transient. Any other 403 is an auth error (a missing scope does not fix itself).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import TYPE_CHECKING, Any

import httpx

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import InboundMessage, MessageSnapshot, SendReceipt
from dispute_agent.kernel.domain.ids import IdempotencyKey
from dispute_agent.kernel.domain.values import UntrustedText

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.external import OutboundMessage
    from dispute_agent.modules.email.gmail_api.auth import GmailAuth

MODULE = "email"
ADAPTER = "gmail_api"

API = "https://gmail.googleapis.com/gmail/v1/users/me"
IDEMPOTENCY_HEADER = "X-Dispute-Idempotency-Key"
MESSAGE_ID_DOMAIN = "dispute-agent.local"
#: How far back the header scan looks when the Message-ID search misses.
SCAN_QUERY = "in:sent newer_than:7d"
SCAN_LIMIT = 25
RATE_LIMITED = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
POSTMASTERS = frozenset({"postmaster", "mailer-daemon", "mailerdaemon"})
_OUR_MESSAGE_ID = re.compile(r"wd1-([0-9a-f]{64})@" + re.escape(MESSAGE_ID_DOMAIN))


def message_id_for(key: IdempotencyKey) -> str:
    """The Message-ID derived from the executor's key: `<wd1-<hex>@dispute-agent.local>`."""
    return f"<{str(key).replace(':', '-', 1)}@{MESSAGE_ID_DOMAIN}>"


def key_from_message_id(value: str) -> IdempotencyKey | None:
    match = _OUR_MESSAGE_ID.search(value or "")
    return IdempotencyKey(f"wd1:{match.group(1)}") if match else None


@dataclass
class GmailChannel:
    """Implements MessageChannel against one personal Gmail mailbox."""

    auth: GmailAuth
    mailbox: str
    timeout_s: float = 30.0
    name: str = MODULE
    #: Gmail has no idempotency key on send; the executor's look-before-write prevents repeats.
    idempotent_writes: bool = False
    lookup_supported: bool = True
    client: httpx.Client | None = field(default=None, repr=False)

    # -- sending -------------------------------------------------------------------------------

    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt:
        raw = base64.urlsafe_b64encode(self.compose(msg, key).as_bytes()).decode("ascii")
        sent = self._request("POST", "/messages/send", json={"raw": raw})
        return SendReceipt(
            external_message_id=str(sent["id"]),
            conversation_id=sent.get("threadId"),
            sent_at=datetime.now(UTC),
        )

    def compose(self, msg: OutboundMessage, key: IdempotencyKey) -> EmailMessage:
        """The RFC 2822 message. Plain text only: the executor hashes what it approved."""
        mail = EmailMessage()
        mail["From"] = self.mailbox
        mail["To"] = msg.to
        mail["Subject"] = msg.subject
        mail["Message-ID"] = message_id_for(key)
        mail[IDEMPOTENCY_HEADER] = str(key)
        if msg.in_reply_to:
            mail["In-Reply-To"] = msg.in_reply_to
            mail["References"] = msg.in_reply_to
        mail.set_content(msg.body, charset="utf-8")
        return mail

    # -- looking up ------------------------------------------------------------------------------

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        """By Message-ID search, then by the header over the last week of Sent. A miss
        settles nothing (the executor treats it so): Gmail's search is not promised instant."""
        msgid = message_id_for(key).strip("<>")
        for item in self._list(f"in:sent rfc822msgid:{msgid}", 2):
            return self._receipt(item)
        for item in self._list(SCAN_QUERY, SCAN_LIMIT):
            meta = self._request(
                "GET",
                f"/messages/{item['id']}",
                params={
                    "format": "metadata",
                    "metadataHeaders": [IDEMPOTENCY_HEADER, "Message-ID"],
                },
            )
            if _key_of(meta) == key:
                return self._receipt(meta)
        return None

    def _receipt(self, item: dict[str, Any]) -> SendReceipt:
        return SendReceipt(
            external_message_id=str(item["id"]),
            conversation_id=item.get("threadId"),
            sent_at=_internal_date(item) or datetime.now(UTC),
        )

    def _list(self, query: str, limit: int) -> list[dict[str, Any]]:
        listed = self._request("GET", "/messages", params={"q": query, "maxResults": limit})
        found = listed.get("messages") or []
        return [m for m in found if isinstance(m, dict)]

    # -- reading back ------------------------------------------------------------------------------

    def get_message(self, receipt: SendReceipt) -> MessageSnapshot:
        raw = self._request(
            "GET", f"/messages/{receipt.external_message_id}", params={"format": "full"}
        )
        if "SENT" not in (raw.get("labelIds") or []):
            raise ExternalNotFound("the message does not carry the SENT label yet")
        headers = _headers(raw)
        return MessageSnapshot(
            external_message_id=str(raw["id"]),
            to=tuple(address for _, address in getaddresses([headers.get("to", "")]) if address),
            subject=headers.get("subject", ""),
            body=_plain_text(raw.get("payload") or {}),
            idempotency_key=_key_of(raw),
            in_sent_items=True,
            conversation_id=raw.get("threadId"),
        )

    # -- polling -----------------------------------------------------------------------------------

    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]:
        """The inbox since `since`, inclusive. Every text arrives as UntrustedText (I5)."""
        # `after:` takes epoch seconds and is exclusive; one second earlier keeps the boundary.
        query = f"in:inbox after:{int(since.timestamp()) - 1}"
        found: list[InboundMessage] = []
        for item in self._list(query, 50):
            raw = self._request("GET", f"/messages/{item['id']}", params={"format": "full"})
            received = _internal_date(raw)
            if received is None or received < since:
                continue
            found.append(self._inbound(raw, received))
        return tuple(sorted(found, key=lambda m: m.received_at))

    def _inbound(self, raw: dict[str, Any], received: datetime) -> InboundMessage:
        headers = _headers(raw)
        message_id = str(raw.get("id", ""))
        sender = parseaddr(headers.get("from", ""))[1]
        body = _plain_text(raw.get("payload") or {})
        bounce = sender.split("@")[0].lower() in POSTMASTERS
        failed: str | None = None
        if bounce:
            # Route a bounce by our own key in the returned headers, never by what it claims.
            key = key_from_message_id(body)
            receipt = self.find_sent(key) if key else None
            failed = receipt.external_message_id if receipt else None
        return InboundMessage(
            external_message_id=message_id,
            internet_message_id=headers.get("message-id", ""),
            conversation_id=raw.get("threadId"),
            from_addr=sender,
            to_addr=parseaddr(headers.get("to", ""))[1],
            subject=UntrustedText.create(headers.get("subject", ""), "seller_email", message_id),
            body=UntrustedText.create(body, "seller_email", message_id),
            received_at=received,
            is_delivery_failure=bounce,
            failed_message_id=failed,
        )

    # -- http ----------------------------------------------------------------------------------------

    def _client(self) -> httpx.Client:
        if self.client is None:
            self.client = httpx.Client(base_url=API, timeout=self.timeout_s)
        return self.client

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self.auth.token()}"}
        try:
            response = self._client().request(method, path, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as unsent:
            raise TransientPortError(
                f"{method} {path} could not be sent: {type(unsent).__name__}"
            ) from unsent
        except httpx.TransportError as lost:
            if method == "GET":
                raise TransientPortError(f"{method} {path} failed: {type(lost).__name__}") from lost
            raise AmbiguousOutcome(
                f"{method} {path}: the request was sent and no answer came back "
                f"({type(lost).__name__}); it may have taken effect"
            ) from lost

        status = response.status_code
        reason = _reason(response)
        if status == 429 or (status == 403 and reason in RATE_LIMITED):
            raise TransientPortError(f"Gmail rate limit ({status} {reason or ''})".strip())
        if status in (401, 403):
            raise AuthPortError(f"Gmail refused the request ({status} {reason or ''})".strip())
        if status == 404:
            raise ExternalNotFound(f"{method} {path} returned 404")
        if status >= 500:
            if method == "GET":
                raise TransientPortError(f"server error {status} on a read")
            raise AmbiguousOutcome(
                f"{method} {path}: server error {status} after the request was accepted; "
                "it may have taken effect"
            )
        if status >= 400:
            raise PermanentPortError(
                f"Gmail refused {method} {path}: {reason or status}", status=status
            )
        if not response.content:
            return {}
        parsed = response.json()
        return dict(parsed) if isinstance(parsed, dict) else {}


# -- reading what came back -------------------------------------------------------------------------


def _headers(raw: dict[str, Any]) -> dict[str, str]:
    payload = raw.get("payload") or {}
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in payload.get("headers") or []
        if isinstance(h, dict)
    }


def _key_of(raw: dict[str, Any]) -> IdempotencyKey | None:
    headers = _headers(raw)
    header = headers.get(IDEMPOTENCY_HEADER.lower())
    if header:
        return IdempotencyKey(header.strip())
    return key_from_message_id(headers.get("message-id", ""))


def _plain_text(part: dict[str, Any]) -> str:
    """The first text/plain part, decoded. Gmail returns body data base64url-encoded."""
    if str(part.get("mimeType", "")).startswith("text/plain"):
        data = (part.get("body") or {}).get("data")
        if data:
            padded = str(data) + "=" * (-len(str(data)) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    for child in part.get("parts") or []:
        text = _plain_text(child)
        if text:
            return text
    return ""


def _internal_date(raw: dict[str, Any]) -> datetime | None:
    value = raw.get("internalDate")
    try:
        return datetime.fromtimestamp(int(str(value)) / 1000, tz=UTC) if value else None
    except ValueError:
        return None


def _reason(response: httpx.Response) -> str | None:
    """Google's error reason, never the request (which carries the bearer token)."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None
    for item in error.get("errors") or []:
        if isinstance(item, dict) and item.get("reason"):
            return str(item["reason"])
    status = error.get("status")
    return str(status) if status else None


__all__ = [
    "ADAPTER",
    "API",
    "IDEMPOTENCY_HEADER",
    "MODULE",
    "GmailChannel",
    "key_from_message_id",
    "message_id_for",
]
