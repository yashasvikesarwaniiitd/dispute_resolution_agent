"""The email module, switched off (I6).

A write module, so policy stops the case at planning with `module_unavailable:email` before
anything is attempted. This adapter is the second line: if a code path reaches it anyway, it
raises rather than quietly doing nothing, because "the message was never sent" and "the
message was sent and we lost the receipt" must never become the same outcome (I4).

The capability flags are False. An adapter that cannot write has no idempotent writes to
offer and nothing to look up, and the executor reads those flags to decide whether a retry
is safe.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.external import (
        InboundMessage,
        MessageSnapshot,
        OutboundMessage,
        SendReceipt,
    )
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "email"


class DisabledMessageChannel:
    """Implements MessageChannel."""

    idempotent_writes = False
    lookup_supported = False

    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt:
        raise ModuleUnavailable(MODULE)

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        raise ModuleUnavailable(MODULE)

    def get_message(self, receipt: SendReceipt) -> MessageSnapshot:
        raise ModuleUnavailable(MODULE)

    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]:
        raise ModuleUnavailable(MODULE)
