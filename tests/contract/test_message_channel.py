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
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import MessageChannel

ADAPTERS = [
    pending("fake", phase="Phase 3 (card P3-02)"),
    pending("outlook_graph", phase="Phase 3 (card P3-03)", integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def channel(request: pytest.FixtureRequest) -> MessageChannel:
    return cast("MessageChannel", build_or_skip(request.param))


class TestCapabilities:
    def test_it_declares_whether_writes_are_idempotent(self, channel: MessageChannel) -> None:
        assert isinstance(channel.idempotent_writes, bool)
        assert isinstance(channel.lookup_supported, bool)


class TestSending:
    def test_a_send_returns_a_receipt_naming_the_message(self, channel: MessageChannel) -> None: ...

    def test_the_same_key_twice_sends_once(self, channel: MessageChannel) -> None: ...

    def test_the_second_send_reports_itself_as_a_duplicate(
        self, channel: MessageChannel
    ) -> None: ...

    def test_a_timeout_raises_transient_not_permanent(self, channel: MessageChannel) -> None: ...


class TestLookup:
    def test_find_sent_returns_the_earlier_receipt(self, channel: MessageChannel) -> None: ...

    def test_find_sent_returns_none_for_an_unused_key(self, channel: MessageChannel) -> None: ...

    def test_a_draft_only_match_raises_ambiguous_outcome(self, channel: MessageChannel) -> None:
        """integrations.md 1.4. The case that keeps I4 honest."""


class TestReadBack:
    def test_get_message_returns_the_text_exactly_as_sent(self, channel: MessageChannel) -> None:
        """I13: the executor hashes this and compares it with what was approved."""

    def test_get_message_raises_external_not_found_before_it_is_visible(
        self, channel: MessageChannel
    ) -> None: ...


class TestPolling:
    def test_fetch_replies_returns_untrusted_text(self, channel: MessageChannel) -> None:
        """I5: subject and body arrive labelled, from the adapter outwards."""

    def test_fetch_replies_is_inclusive_of_the_since_boundary(
        self, channel: MessageChannel
    ) -> None: ...

    def test_a_delivery_failure_names_the_message_that_failed(
        self, channel: MessageChannel
    ) -> None: ...
