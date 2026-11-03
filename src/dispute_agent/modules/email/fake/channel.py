"""A fake mailbox that behaves like Outlook, including the parts that are inconvenient.

**A fake kinder than the system it stands for makes every test that uses it meaningless.**
That is the whole risk of this file, so the awkward behaviours are modelled on purpose:

- A send is a **draft, then a send** (ADR 0019). The two steps can fail independently, and
  the gap between them is where `AmbiguousOutcome` lives: a message that exists in Drafts
  carrying our idempotency key is not evidence that anything was sent.
- A sent message is **not immediately readable back**. Real Exchange moves the draft to Sent
  Items asynchronously, so `get_message` raises `ExternalNotFound` until it lands. A fake
  that returned the message straight away would make the executor's verification window
  look unnecessary.
- **"Sent" is never "delivered"** (ADR 0010). Nothing here reports delivery, because nothing
  at the far end can.
- The body is **normalised, not echoed**. Graph does not promise a byte-for-byte round trip
  for message bodies, so this mangles whitespace the way a real mail system would, and the
  executor's hash comparison has to cope.

Faults are injected by token, as in the synthetic evidence source, so a scenario can ask for
exactly the failure it wants to test.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    ModuleUnavailable,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import MessageSnapshot, SendReceipt

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.external import InboundMessage, OutboundMessage
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "email"
ADAPTER = "fake"

#: How long a sent message stays invisible to read-back, standing in for the asynchronous
#: move from Drafts to Sent Items. Short enough for tests, long enough to be observable.
SEND_VISIBILITY_DELAY = timedelta(seconds=2)


@dataclass(frozen=True)
class StoredMessage:
    """One message in the fake mailbox, with the folder it is actually in."""

    receipt: SendReceipt
    message: OutboundMessage
    key: IdempotencyKey
    folder: str  # "drafts" | "sentitems"
    created_at: datetime


def normalise(text: str) -> str:
    """What a mail system does to a body on the way through (integrations.md section 1.4).

    NFC, CRLF to LF, trailing whitespace per line removed, runs of blank lines collapsed,
    outer whitespace stripped. The executor normalises both sides before hashing for exactly
    this reason; this fake applies it so that a test can tell the difference between "the
    text survived" and "the text was echoed back untouched".
    """
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


@dataclass
class FakeMessageChannel:
    """Implements MessageChannel.

    `faults` maps a fault token to the behaviour it causes on the *next* matching call:

    - `timeout` — `send` raises `TransientPortError` after the draft exists and was sent,
      which is scenario S12a: the message really went, and only the answer was lost.
    - `stuck_in_drafts` — the draft is created and `/send` never runs, which is S12b: the
      only honest answer to `find_sent` is `AmbiguousOutcome`.
    - `permanent` — a 400-class refusal, which is never retried.
    - `auth` — credentials no longer work; the case escalates rather than retrying forever.
    - `unavailable` — the module is switched off (I6).
    """

    mailbox: str = "agent@example.invalid"
    #: False, because Graph has none (integrations.md section 1.2). An earlier version
    #: of this fake said True and deduplicated on the key, which is precisely the
    #: "kinder than the real thing" failure this file's docstring warns about - and on
    #: the one dimension that produces a second email. Email deduplication comes from
    #: the executor reading its own action record first, never from the mailbox.
    idempotent_writes: bool = False
    lookup_supported: bool = True
    faults: dict[str, str] = field(default_factory=dict)
    name: str = MODULE
    #: Set by a test to make visibility deterministic; otherwise the wall clock is used.
    now: datetime | None = None

    _all: list[StoredMessage] = field(default_factory=list)
    _inbox: list[InboundMessage] = field(default_factory=list)
    _send_calls: int = 0

    # -- writing --------------------------------------------------------------------------

    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt:
        """Draft, then send. The two can fail apart, and that gap is the point."""
        self._send_calls += 1
        self._check_module()
        fault = self.faults.get("send")

        if fault == "auth":
            raise AuthPortError("the mailbox credentials are no longer valid")
        if fault == "permanent":
            raise PermanentPortError("the recipient was refused", status=400)

        # No check for a reused key on purpose. Graph has no idempotency key, so a second
        # send really does produce a second email, and a fake that quietly deduplicated
        # would hide the whole reason the executor looks up before it writes.
        moment = self._moment()
        receipt = SendReceipt(
            external_message_id=f"msg-{len(self._all) + 1:04d}",
            conversation_id=f"conv-{msg.case_id}",
            sent_at=moment,
        )

        if fault == "stuck_in_drafts":
            # The draft exists and carries our key; `/send` never ran. Nothing here may be
            # reported as sent, and a later run must not send the draft (integrations.md
            # section 1.3) - a human resolves it.
            self._all.append(StoredMessage(receipt, msg, key, "drafts", moment))
            raise TransientPortError("the send did not complete")

        self._all.append(StoredMessage(receipt, msg, key, "sentitems", moment))

        if fault == "timeout":
            # S12a: it really was sent, and only the answer was lost. That is an ambiguous
            # write, not a transient one - TransientPortError promises "certainly did not
            # happen", and this fake used to break that promise in exactly the way the real
            # adapters did. The executor must find it by lookup rather than send again.
            raise AmbiguousOutcome(
                "timed out waiting for the send to be acknowledged",
                external_ref=receipt.external_message_id,
            )

        return receipt

    # -- looking up -----------------------------------------------------------------------

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        """Sent Items first, then Drafts. A draft alone is not proof of a send."""
        self._check_module()
        if self.faults.get("find_sent") == "transient":
            raise TransientPortError("the lookup could not be completed")

        matching = [s for s in self._all if s.key == key]
        if not matching:
            return None
        sent = [s for s in matching if s.folder == "sentitems"]
        if sent:
            # The first one, so a key that somehow produced two is reported as the earliest
            # send rather than the latest - the executor is asking "did this go out?".
            return sent[0].receipt
        raise AmbiguousOutcome(
            "a draft carrying this key exists but nothing is in Sent Items",
            external_ref=matching[0].receipt.external_message_id,
        )

    def get_message(self, receipt: SendReceipt) -> MessageSnapshot:
        """Read-back (I2), and not before the message has actually landed."""
        self._check_module()
        stored = next(
            (s for s in self._all if s.receipt.external_message_id == receipt.external_message_id),
            None,
        )
        if stored is None:
            raise ExternalNotFound("no message with that id")
        if stored.folder != "sentitems":
            # With immutable ids the id resolves to the Drafts copy until the move happens.
            # Raising keeps the case waiting rather than verifying or failing early.
            raise ExternalNotFound("the message is still in Drafts")
        if self._moment() < stored.created_at + SEND_VISIBILITY_DELAY:
            raise ExternalNotFound("the message has not appeared in Sent Items yet")

        return MessageSnapshot(
            external_message_id=stored.receipt.external_message_id,
            to=(stored.message.to,),
            subject=stored.message.subject,
            # Normalised, not echoed: a real mail system does not promise the bytes back.
            body=normalise(stored.message.body),
            idempotency_key=stored.key,
            in_sent_items=True,
            conversation_id=stored.receipt.conversation_id,
        )

    # -- reading --------------------------------------------------------------------------

    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]:
        """Polling, not webhooks (ADR 0022)."""
        self._check_module()
        if self.faults.get("fetch_replies") == "transient":
            raise TransientPortError("the mailbox could not be read")
        if self.faults.get("fetch_replies") == "auth":
            raise AuthPortError("the mailbox credentials are no longer valid")
        # Inclusive of the boundary: a message arriving in the same instant as the last
        # one seen must not be lost. Duplicates are cheap - the inbox deduplicates by
        # message id - and a missed reply leaves a seller waiting on a case that has
        # stopped listening.
        return tuple(m for m in self._inbox if m.received_at >= since)

    # -- test affordances -------------------------------------------------------------------

    def deliver(self, message: InboundMessage) -> None:
        """Put an inbound message in the mailbox. Tests only; no port method writes here."""
        self._inbox.append(message)

    def drafts(self) -> tuple[StoredMessage, ...]:
        """Everything stuck in Drafts, for a test asserting nothing was left behind."""
        return tuple(s for s in self._all if s.folder == "drafts")

    def sent_count(self) -> int:
        """How many messages actually exist in Sent Items.

        The number that matters: the duplicate-side-effect gate is this being 1 after a
        retry, not the send method returning something reassuring.
        """
        return len([s for s in self._all if s.folder == "sentitems"])

    def send_attempts(self) -> int:
        return self._send_calls

    # -- helpers ----------------------------------------------------------------------------

    def _check_module(self) -> None:
        if self.faults.get("module") == "unavailable":
            raise ModuleUnavailable(MODULE)

    def _moment(self) -> datetime:
        return self.now or datetime.now(UTC)


__all__ = ["ADAPTER", "MODULE", "SEND_VISIBILITY_DELAY", "FakeMessageChannel", "normalise"]
