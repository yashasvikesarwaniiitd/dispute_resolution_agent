"""Contract: TicketingSystem (kernel-interfaces.md 6.4, integrations.md 2).

The asymmetry is the thing to get right. `find_issue` is best effort: a hit is reliable, a
miss is not proof of absence (integrations.md 2.2), so a miss alone must never let the
executor write again. A fake that answers definitively both ways would hide the only
behaviour that makes this port different from the others.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import TicketingSystem

ADAPTERS = [
    pending("fake", phase="Phase 3 (card P3-02)"),
    pending("jira_cloud", phase="Phase 3 (card P3-04)", integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def ticketing(request: pytest.FixtureRequest) -> TicketingSystem:
    return cast("TicketingSystem", build_or_skip(request.param))


class TestCapabilities:
    def test_it_declares_its_capabilities(self, ticketing: TicketingSystem) -> None:
        assert isinstance(ticketing.idempotent_writes, bool)
        assert isinstance(ticketing.lookup_supported, bool)


class TestUpsert:
    def test_an_upsert_returns_a_reference(self, ticketing: TicketingSystem) -> None: ...

    def test_the_same_key_twice_creates_one_issue(self, ticketing: TicketingSystem) -> None:
        """ADR 0020: the idempotency label is how a repeat is recognised."""

    def test_the_second_upsert_reports_itself_as_a_duplicate(
        self, ticketing: TicketingSystem
    ) -> None: ...


class TestLookup:
    def test_a_hit_is_reliable(self, ticketing: TicketingSystem) -> None: ...

    def test_a_miss_is_not_treated_as_proof_of_absence(self, ticketing: TicketingSystem) -> None:
        """integrations.md 2.2: search indexing lags, so a miss means "do not know"."""


class TestReadBack:
    def test_get_issue_returns_plain_text_from_the_stored_adf(
        self, ticketing: TicketingSystem
    ) -> None:
        """integrations.md 2.3: normalised, so the read-back comparison is like for like."""

    def test_get_issue_carries_the_idempotency_key_back(
        self, ticketing: TicketingSystem
    ) -> None: ...


class TestFindings:
    def test_fetch_findings_returns_untrusted_notes(self, ticketing: TicketingSystem) -> None:
        """I5: an ops note is written by a person at a hub, so it is outside text."""

    def test_measurements_come_back_as_decimals_not_floats(
        self, ticketing: TicketingSystem
    ) -> None:
        """I9: a remeasurement feeds the calculator."""
