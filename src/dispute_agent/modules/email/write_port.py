"""Presenting a `MessageChannel` as the executor's `WritePort` (P3-04).

The executor knows three verbs — send with a key, look up by that key, read back — and
nothing about mail. The channel port speaks in messages. This is the twenty lines that join
them, and it exists rather than being folded into either side so that the executor stays
free of email and the adapter stays free of the executor.

It does one thing beyond renaming. **The body it hands back is normalised**, because that is
the form the executor compares against the approved text. Graph does not promise a message
body round-trips byte for byte, so comparing raw text would fail verification on messages
that are entirely correct — and a system that reports correct sends as unverified teaches
whoever reads the queue to ignore it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.external import OutboundMessage
from dispute_agent.modules.email.outlook_graph.channel import normalise

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import MessagePayload
    from dispute_agent.kernel.domain.external import MessageSnapshot, SendReceipt
    from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
    from dispute_agent.kernel.ports import MessageChannel


@dataclass(frozen=True)
class NormalisedMessage:
    """A snapshot whose body is in the form the comparison uses."""

    external_message_id: str
    to: tuple[str, ...]
    subject: str
    body: str
    idempotency_key: IdempotencyKey | None
    in_sent_items: bool
    conversation_id: str | None


@dataclass
class MessageWritePort:
    """Implements WritePort over any MessageChannel, real or fake.

    `send` takes the intent's own `MessagePayload` - the approved plan, whose hash the
    executor has just checked - and builds the outbound message from it here. So the only
    text that can reach the channel is text that matched the approval (I13). It used to be
    handed a ready-made message by the caller, and nothing tied that to the approval.
    """

    channel: MessageChannel
    case_id: CaseId

    @property
    def idempotent_writes(self) -> bool:
        return self.channel.idempotent_writes

    @property
    def lookup_supported(self) -> bool:
        return self.channel.lookup_supported

    def send(self, payload: Any, key: IdempotencyKey) -> SendReceipt:
        approved: MessagePayload = payload
        return self.channel.send(
            OutboundMessage(
                to=approved.to,
                subject=approved.subject,
                body=approved.body,
                in_reply_to=approved.in_reply_to,
                case_id=self.case_id,
                kind=approved.kind,
            ),
            key,
        )

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        return self.channel.find_sent(key)

    def read_back(self, receipt: Any) -> NormalisedMessage:
        snapshot: MessageSnapshot = self.channel.get_message(receipt)
        return NormalisedMessage(
            external_message_id=snapshot.external_message_id,
            to=snapshot.to,
            subject=snapshot.subject,
            # The one transformation. Everything else is a rename.
            body=normalise(snapshot.body),
            idempotency_key=snapshot.idempotency_key,
            in_sent_items=snapshot.in_sent_items,
            conversation_id=snapshot.conversation_id,
        )


__all__ = ["MessageWritePort", "NormalisedMessage"]
