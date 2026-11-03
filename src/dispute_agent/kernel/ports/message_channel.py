"""The MessageChannel port (kernel-interfaces.md section 6.4).

A write port, so it has the shape every write port has: send, look up by idempotency key,
read back. The two capability flags are not decoration — the executor reads them to decide
whether a retry is safe and whether a lookup settles anything (I2, I4).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from dispute_agent.kernel.domain.external import (
    InboundMessage,
    MessageSnapshot,
    OutboundMessage,
    SendReceipt,
)
from dispute_agent.kernel.domain.ids import IdempotencyKey


class MessageChannel(Protocol):
    idempotent_writes: bool
    """True if send() with a reused key cannot send twice."""

    lookup_supported: bool
    """True if find_sent() works at all."""

    def send(self, msg: OutboundMessage, key: IdempotencyKey) -> SendReceipt:
        """Raises TransientPortError, AmbiguousOutcome, PermanentPortError, AuthPortError,
        ModuleUnavailable. Timeout: executor.call_timeout_s."""
        ...

    def find_sent(self, key: IdempotencyKey) -> SendReceipt | None:
        """The message previously sent with this key, if visible.

        Raises TransientPortError, and AmbiguousOutcome when the evidence cannot settle it —
        a marked message found only in Drafts is not proof that anything was sent.
        """
        ...

    def get_message(self, receipt: SendReceipt) -> MessageSnapshot:
        """Read-back (I2). Raises ExternalNotFound (not yet visible), TransientPortError."""
        ...

    def fetch_replies(self, since: datetime) -> Sequence[InboundMessage]:
        """Polling, not webhooks (ADR 0022). Raises TransientPortError, AuthPortError,
        ModuleUnavailable."""
        ...
