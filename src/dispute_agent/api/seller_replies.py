"""The seller's reply, from the mailbox into the case (ADR 0053).

The mail adapter could read the inbox (`fetch_replies`) but nothing ever called it, so a
seller's reply sat in Gmail while the case waited for it and then closed as "no response".
This job runs on the runner's tick. Every `every` it reads the inbox since its last read and
submits each message as an inbound event:

- a bounce as DELIVERY_FAILURE_RECEIVED, routed by the failed message's own id;
- anything else as SELLER_REPLY_RECEIVED. Dispatch routes it (thread, or exactly one open
  case for that sender and a shipment named in the subject); a message that matches no case
  is kept in the inbox as UNROUTABLE for a person, never guessed onto a case.

Every word of a reply arrives as UntrustedText (I5). The message id is the dedupe key, so an
overlapping read delivers nothing twice. The adapter has already left out the agent's own
emails.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog

from dispute_agent.kernel.domain.enums import ActorKind, InboundEventType
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.inbound import DeliveryFailureReceived, SellerReplyReceived
from dispute_agent.kernel.domain.models import Actor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.external import InboundMessage

log = structlog.get_logger(__name__)

SELLER = Actor(kind=ActorKind.EXTERNAL, id="seller")
MAILBOX = Actor(kind=ActorKind.EXTERNAL, id="mailbox")
FIRST_LOOK_BACK = timedelta(days=2)
OVERLAP = timedelta(minutes=5)


def as_event(message: InboundMessage, source: str) -> InboundEvent | None:
    """One mailbox message as the inbound event dispatch understands, or None to skip it."""
    if message.is_delivery_failure:
        if not message.failed_message_id:
            return None  # a bounce we cannot tie to a message we sent: nothing to route by
        return InboundEvent(
            type=InboundEventType.DELIVERY_FAILURE_RECEIVED,
            source=source,
            source_event_id=message.external_message_id,
            occurred_at=message.received_at,
            actor=MAILBOX,
            payload=DeliveryFailureReceived(
                failed_external_message_id=message.failed_message_id,
                diagnostic_code="bounced",
            ),
        )
    if len(message.from_addr.strip()) < 3:
        return None
    return InboundEvent(
        type=InboundEventType.SELLER_REPLY_RECEIVED,
        source=source,
        source_event_id=message.external_message_id,
        occurred_at=message.received_at,
        actor=SELLER,
        payload=SellerReplyReceived(
            external_message_id=message.external_message_id,
            internet_message_id=message.internet_message_id,
            conversation_id=message.conversation_id,
            from_addr=message.from_addr,
            subject=message.subject,
            body=message.body,
            attachments=tuple(a.name for a in message.attachments),
            received_at=message.received_at,
            sender_auth=message.auth_result,
        ),
    )


@dataclass
class SellerRepliesJob:
    fetch: Callable[[datetime], Sequence[InboundMessage]]
    submit: Callable[[InboundEvent], object]
    source: str = "gmail"
    every: timedelta = timedelta(seconds=60)
    name: str = "seller_replies"
    _last_run: datetime | None = field(default=None, repr=False)
    _since: datetime | None = field(default=None, repr=False)

    def due(self, now: datetime) -> bool:
        return self._last_run is None or now - self._last_run >= self.every

    def run(self, now: datetime) -> int:
        """Submit every new message; return how many were submitted."""
        self._last_run = now  # a failing read waits for the next interval, not the next tick
        since = (self._since or now - FIRST_LOOK_BACK) - OVERLAP
        submitted = 0
        for message in self.fetch(since):
            event = as_event(message, self.source)
            if event is None:
                continue
            result = self.submit(event)
            submitted += 1
            log.info(
                "seller_reply_received",
                kind=event.type.value,
                reaction=str(getattr(result, "reaction", "")),
            )
        self._since = now
        return submitted


__all__ = ["SellerRepliesJob", "as_event"]
