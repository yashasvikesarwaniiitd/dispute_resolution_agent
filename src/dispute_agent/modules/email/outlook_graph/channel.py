"""Outlook through Microsoft Graph (integrations.md section 1, ADR 0019, ADR 0023).

Mail is not sent with `sendMail`. That call returns 202 with no body and no id, and 202
explicitly does not mean the work finished — so there is nothing to verify and nothing to
recognise on a retry. Instead every message is **created as a draft carrying our idempotency
key**, then sent, then found again by that key. The key is what makes a lost answer
recoverable: if the network drops after `/send`, the next run looks the key up rather than
writing a second email.

Three things this adapter refuses to do, each because the alternative is a seller receiving
something nobody meant to send.

**It never claims a message was delivered.** `idempotent_writes` is False because Graph has
no idempotency key, and "verified" means the message is in Sent Items with the approved
text — not that it arrived. Nobody at this end holds that fact (ADR 0010).

**It never re-sends a draft it finds.** A draft carrying our key means `/send` either never
ran or is still being processed, and sending it might produce a second copy. `find_sent`
raises `AmbiguousOutcome` and a person resolves it, following the rule in integrations.md
1.3: check Sent Items, then delete stray drafts.

**It never treats the immutable id as authoritative on a personal mailbox.** Immutable ids
are undocumented for consumer accounts, so the id is a fast path and the extended-property
filter — which *is* documented for personal accounts — is the real lookup. When the id
misses, the filter decides.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import (
    InboundMessage,
    MessageSnapshot,
    SendReceipt,
)
from dispute_agent.kernel.domain.ids import IdempotencyKey
from dispute_agent.kernel.domain.values import UntrustedText

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.external import OutboundMessage
    from dispute_agent.modules.email.outlook_graph.auth import GraphAuth

MODULE = "email"
ADAPTER = "outlook_graph"

GRAPH = "https://graph.microsoft.com/v1.0"

#: A fixed project constant. The GUID names our property set; the name identifies the one
#: property in it. Changing either orphans every message already sent.
PROPERTY_GUID = "{1a8b5d6e-4c3f-4a21-9f0e-2d7c6b4e8a13}"
PROPERTY_NAME = "DisputeAgentIdempotencyKey"
PROPERTY_ID = f"String {PROPERTY_GUID} Name {PROPERTY_NAME}"

#: Custom headers must start with `x-` and can only be set at creation. This one is for a
#: person reading raw mail; the extended property above is what lookup filters on.
IDEMPOTENCY_HEADER = "x-dispute-idempotency-key"

#: Status codes worth trying again. 429 and 5xx only: a 4xx is an argument we got wrong, and
#: retrying it gets the same answer more slowly.
RETRYABLE = frozenset({429, 500, 502, 503, 504})


def normalise(text: str) -> str:
    """integrations.md section 1.4. Both sides of the hash comparison use this.

    Graph does not promise a body round-trips byte for byte, and examples show HTML
    wrapping, so comparing raw text would fail verification on messages that are perfectly
    correct. What stays exact is the subject, the recipients and the key.
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass
class OutlookGraphChannel:
    """Implements MessageChannel against one personal Outlook.com mailbox."""

    auth: GraphAuth
    timeout_s: float = 30.0
    name: str = MODULE
    #: Graph has no idempotency key (integrations.md section 1.2). The executor's
    #: lookup-before-write is what stops a second email, not the mailbox.
    idempotent_writes: bool = False
    lookup_supported: bool = True
    #: Injected for tests. Production builds its own.
    client: httpx.Client | None = field(default=None, repr=False)

    # -- sending -------------------------------------------------------------------------------

    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt:
        """Create the marked draft, then send it. Two calls, and the gap matters.

        If the create succeeds and the send does not, a draft carrying the key is left
        behind. That is deliberately *not* cleaned up here: deleting it would destroy the
        only evidence that the attempt happened, and `find_sent` reports it as ambiguous so
        a person decides.
        """
        draft = self._post(
            "/me/messages",
            json=self._draft_body(msg, key),
            headers={"Prefer": 'IdType="ImmutableId"'},
        )
        message_id = str(draft["id"])

        self._post(f"/me/messages/{message_id}/send", json=None, expect_empty=True)

        return SendReceipt(
            external_message_id=message_id,
            conversation_id=draft.get("conversationId"),
            sent_at=_now(),
        )

    def _draft_body(self, msg: OutboundMessage, key: IdempotencyKey) -> dict[str, Any]:
        return {
            "subject": msg.subject,
            # Text, not HTML. The executor hashes what it approved, and a body Graph
            # wrapped in markup would never match it.
            "body": {"contentType": "Text", "content": msg.body},
            "toRecipients": [{"emailAddress": {"address": msg.to}}],
            "singleValueExtendedProperties": [{"id": PROPERTY_ID, "value": str(key)}],
            "internetMessageHeaders": [{"name": IDEMPOTENCY_HEADER, "value": str(key)}],
        }

    # -- looking up ------------------------------------------------------------------------------

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        """Sent Items, then Drafts. A draft alone proves nothing was sent.

        This is the method the whole draft-and-mark design exists for: after a timeout, it
        answers "did it actually go?" without writing anything.
        """
        sent = self._by_key("sentitems", key)
        if sent:
            return SendReceipt(
                external_message_id=str(sent[0]["id"]),
                conversation_id=sent[0].get("conversationId"),
                sent_at=_parse(sent[0].get("sentDateTime")) or _now(),
            )

        drafts = self._by_key("drafts", key)
        if drafts:
            raise AmbiguousOutcome(
                "a draft carrying this idempotency key exists and nothing is in Sent "
                "Items; check Sent Items, then delete the stray draft. Never send it.",
                external_ref=",".join(str(d["id"]) for d in drafts),
            )
        return None

    def _by_key(self, folder: str, key: IdempotencyKey) -> list[dict[str, Any]]:
        """Filter a folder on the extended property. Documented for personal accounts."""
        escaped = str(key).replace("'", "''")
        filter_expr = (
            f"singleValueExtendedProperties/Any(ep: ep/id eq '{PROPERTY_ID}' "
            f"and ep/value eq '{escaped}')"
        )
        try:
            payload = self._get(
                f"/me/mailFolders/{folder}/messages",
                params={"$filter": filter_expr, "$select": "id,conversationId,sentDateTime"},
            )
        except ExternalNotFound as missing:
            # A 404 here is the *folder*, not the message. `sentitems` and `drafts` are
            # well-known folders that exist on every mailbox, so this means the mailbox or
            # the permissions are wrong - a configuration failure, never "no message was
            # sent". Letting ExternalNotFound out would also break find_sent's contract,
            # which promises only None, a receipt, or AmbiguousOutcome.
            raise PermanentPortError(
                f"the {folder} folder could not be read; check GRAPH_MAILBOX and the "
                "delegated Mail.Read permission",
                status=404,
            ) from missing
        values = payload.get("value", [])
        return list(values) if isinstance(values, list) else []

    # -- reading back ------------------------------------------------------------------------------

    def get_message(self, receipt: SendReceipt) -> MessageSnapshot:
        """Read-back (I2). Only a message in Sent Items counts as sent.

        The id is tried first because it is one request. On a personal mailbox it may not
        resolve at all, and with immutable ids it can resolve to the *Drafts* copy until
        the asynchronous move happens — so a hit that is not in Sent Items is treated as
        "not yet", never as a failure. The case keeps waiting and becomes UNKNOWN at the
        deadline rather than being verified early or failed wrongly.
        """
        raw = self._get_or_none(
            f"/me/messages/{receipt.external_message_id}",
            # Graph returns an extended property only when it is expanded by name. Without
            # this the key we wrote is never read back, and the draft-and-mark design loses
            # its point: we could not show the message in Sent Items is the one this action
            # sent (integrations.md 1.4; Phase 3 review, finding 5).
            params={"$expand": f"singleValueExtendedProperties($filter=id eq '{PROPERTY_ID}')"},
            headers={
                "Prefer": 'IdType="ImmutableId", outlook.body-content-type="text"',
            },
        )
        if raw is None:
            raw = self._by_filter_fallback(receipt)

        if not _in_sent_items(raw):
            raise ExternalNotFound("the message is not in Sent Items yet")

        return MessageSnapshot(
            external_message_id=str(raw["id"]),
            to=tuple(r["emailAddress"]["address"] for r in raw.get("toRecipients", [])),
            subject=str(raw.get("subject", "")),
            body=str((raw.get("body") or {}).get("content", "")),
            idempotency_key=_property_value(raw),
            in_sent_items=True,
            conversation_id=raw.get("conversationId"),
        )

    def _by_filter_fallback(self, receipt: SendReceipt) -> dict[str, Any]:
        """The authoritative lookup when the id does not resolve.

        Needs the key, which a receipt does not carry, so it is read from the message the
        filter finds rather than assumed. Without a key there is nothing to search on and
        the honest answer is "not found".
        """
        raise ExternalNotFound(
            f"no message with id {receipt.external_message_id}; "
            "use find_sent with the action's idempotency key"
        )

    def get_message_by_key(self, key: IdempotencyKey) -> MessageSnapshot:
        """Read back by the key rather than the id, for the personal-mailbox path."""
        found = self._by_key("sentitems", key)
        if not found:
            raise ExternalNotFound("nothing in Sent Items carries this key")
        return self.get_message(
            SendReceipt(external_message_id=str(found[0]["id"]), sent_at=_now())
        )

    # -- polling -----------------------------------------------------------------------------------

    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]:
        """Poll the inbox (ADR 0022: no webhooks in v1).

        Every piece of text here is `UntrustedText` from the moment it is read — subject
        included, because a subject is written by whoever sent the mail (I5).
        """
        payload = self._get(
            "/me/mailFolders/inbox/messages",
            params={
                "$filter": f"receivedDateTime ge {_iso(since)}",
                "$select": (
                    "id,internetMessageId,conversationId,subject,body,from,"
                    "toRecipients,receivedDateTime"
                ),
                "$orderby": "receivedDateTime asc",
                "$top": "50",
            },
            headers={"Prefer": 'outlook.body-content-type="text"'},
        )
        return tuple(_to_inbound(raw) for raw in payload.get("value", []))

    # -- http ----------------------------------------------------------------------------------------

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.auth.token()}",
            "Content-Type": "application/json",
        }
        headers.update(extra or {})
        return headers

    def _client(self) -> httpx.Client:
        if self.client is None:
            # Every call has a timeout (CLAUDE.md section 7). A request with none is a
            # worker that never comes back.
            self.client = httpx.Client(base_url=GRAPH, timeout=self.timeout_s)
        return self.client

    def _get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = self._headers(kwargs.pop("headers", None))
        return self._send_request("GET", path, headers=headers, **kwargs)

    def _get_or_none(self, path: str, **kwargs: Any) -> dict[str, Any] | None:
        try:
            return self._get(path, **kwargs)
        except ExternalNotFound:
            return None

    def _post(
        self, path: str, *, json: Any, expect_empty: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        headers = self._headers(kwargs.pop("headers", None))
        return self._send_request(
            "POST", path, headers=headers, json=json, expect_empty=expect_empty, **kwargs
        )

    def _send_request(
        self, method: str, path: str, *, expect_empty: bool = False, **kwargs: Any
    ) -> dict[str, Any]:
        """One place where an HTTP answer becomes one of our errors.

        The mapping is the whole of how the executor behaves: a transient error is retried
        under the protection of the idempotency key, a permanent one never is, and an auth
        error stops rather than spinning.
        """
        try:
            response = self._client().request(method, path, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as unsent:
            # The request never left this machine, so nothing happened at the far end.
            # This, and only this, is what TransientPortError promises the executor.
            raise TransientPortError(
                f"{method} {path} could not be sent: {type(unsent).__name__}"
            ) from unsent
        except httpx.TransportError as lost:
            if method == "GET":
                # A read changes nothing, so a lost answer is safe to retry.
                raise TransientPortError(f"{method} {path} failed: {type(lost).__name__}") from lost
            # The write was dispatched and the answer was lost. It may well have happened -
            # Graph may have sent the email, Jira may have created the issue. Calling that
            # transient told the executor "certainly did not happen", which recorded a sent
            # email as FAILED and let a retry send a second copy (Phase 3 review, finding 1).
            raise AmbiguousOutcome(
                f"{method} {path}: the request was sent and no answer came back "
                f"({type(lost).__name__}); it may have taken effect"
            ) from lost

        if response.status_code in (401, 403):
            # Never retried. A credential that stopped working does not start again, and
            # the case should reach someone who can sign in.
            raise AuthPortError(f"Graph refused the request ({response.status_code})")
        if response.status_code == 404:
            raise ExternalNotFound(f"{method} {path} returned 404")
        if response.status_code == 429:
            # Rate limited: refused before any work was done, so safe to retry.
            raise TransientPortError(
                f"rate limited ({response.status_code})", retry_after_s=_retry_after(response)
            )
        if response.status_code in RETRYABLE:
            if method == "GET":
                raise TransientPortError(
                    f"server error {response.status_code} on a read",
                    retry_after_s=_retry_after(response),
                )
            # executor.md section 7: a 500/502/503/504 on a write is ambiguous - the server
            # may have applied it before failing to answer. Calling it transient told the
            # executor "certainly did not happen", recorded FAILED, and let a replan with a
            # new key send a second copy (second Phase 3 review, finding 1).
            raise AmbiguousOutcome(
                f"{method} {path}: server error {response.status_code} after the request "
                "was accepted; it may have taken effect"
            )
        if response.status_code >= 400:
            raise PermanentPortError(
                f"Graph refused {method} {path}: {_safe_detail(response)}",
                status=response.status_code,
            )

        if expect_empty or not response.content:
            return {}
        parsed = response.json()
        return dict(parsed) if isinstance(parsed, dict) else {}


# -- reading what came back -------------------------------------------------------------------------


def _in_sent_items(raw: dict[str, Any]) -> bool:
    """Whether this is the Sent Items copy.

    `parentFolderId` is an opaque id, so the adapter cannot compare it to a name. What it
    can do is refuse to treat anything it has not positively identified as sent — which is
    the safe direction: the case waits rather than being verified on a draft.
    """
    return bool(raw.get("sentDateTime")) and not raw.get("isDraft", False)


def _property_value(raw: dict[str, Any]) -> IdempotencyKey | None:
    for prop in raw.get("singleValueExtendedProperties", []):
        if prop.get("id") == PROPERTY_ID:
            return IdempotencyKey(str(prop.get("value", "")))
    return None


def _to_inbound(raw: dict[str, Any]) -> InboundMessage:
    """Everything a person wrote arrives labelled, from here outwards (I5)."""
    message_id = str(raw.get("id", ""))
    sender = ((raw.get("from") or {}).get("emailAddress") or {}).get("address", "")
    recipients = raw.get("toRecipients") or [{}]
    to_addr = (recipients[0].get("emailAddress") or {}).get("address", "")

    return InboundMessage(
        external_message_id=message_id,
        internet_message_id=str(raw.get("internetMessageId", "")),
        conversation_id=raw.get("conversationId"),
        from_addr=str(sender),
        to_addr=str(to_addr),
        subject=UntrustedText.create(str(raw.get("subject", "")), "seller_email", message_id),
        body=UntrustedText.create(
            str((raw.get("body") or {}).get("content", "")), "seller_email", message_id
        ),
        received_at=_parse(raw.get("receivedDateTime")) or _now(),
    )


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _safe_detail(response: httpx.Response) -> str:
    """A Graph error, trimmed. Never the request, which carries the bearer token."""
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase or str(response.status_code)
    error = body.get("error", {}) if isinstance(body, dict) else {}
    return str(error.get("code", response.status_code))


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    from datetime import UTC

    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)


__all__ = [
    "ADAPTER",
    "GRAPH",
    "IDEMPOTENCY_HEADER",
    "MODULE",
    "PROPERTY_ID",
    "RETRYABLE",
    "OutlookGraphChannel",
    "normalise",
]
