"""The seam that lets one contract suite run against a fake and a real service.

A contract suite is only worth having if it contains **no adapter-specific assertion** — the
moment it does, "a new adapter is done when it passes the suite" stops being true. But some
cases cannot be reached through the port at all: there is no port method for "put the message
in Drafts and leave it there", and there must not be, because that is not something the
system is allowed to do on purpose.

So each adapter supplies a small harness instead. The suite asks the harness for a channel in
a given condition and makes its assertions through the port alone. A harness that cannot
produce a condition says so, and that one case skips with a reason — which is honest, and
much better than the suite quietly asserting less against the real thing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

import pytest

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.external import InboundMessage


class UnsupportedError(Exception):
    """This harness cannot produce that condition. The case skips, naming why."""


class ChannelHarness(Protocol):
    """How a suite gets a `MessageChannel` into the state it wants to test."""

    name: str

    def channel(self, **conditions: str) -> Any:
        """A channel, optionally primed to fail. Raises `UnsupportedError` for a condition it
        cannot arrange."""
        ...

    def deliver(self, channel: Any, message: InboundMessage) -> None:
        """Make an inbound message appear. Raises `UnsupportedError` if it cannot."""
        ...

    def at(self, channel: Any, moment: datetime) -> None:
        """Move the adapter's idea of now, so visibility delays can be crossed."""
        ...

    def sent_count(self, channel: Any) -> int:
        """How many messages really exist at the far end.

        The duplicate-side-effect gate reads this rather than a receipt, because a receipt
        is what an adapter chose to tell us and this is what is actually out there.
        """
        ...


class TicketHarness(Protocol):
    """The same, for `TicketingSystem`."""

    name: str

    def system(self, **conditions: str) -> Any: ...

    def at(self, system: Any, moment: datetime) -> None: ...

    def issue_count(self, system: Any) -> int: ...


def arrange(call: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a harness call, turning `UnsupportedError` into a skip that names the reason."""
    try:
        return call(*args, **kwargs)
    except UnsupportedError as unsupported:
        pytest.skip(f"this adapter cannot arrange it: {unsupported}")
