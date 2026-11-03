"""Contract: CaseRepository and EventLog (kernel-interfaces.md 6.2, schema.sql).

One suite, because they are written together and a test of one almost always needs the
other. The cases that matter are the concurrency ones: optimistic versioning is only a
guarantee if a stale save really fails, and `EventLog.append` assigning sequence numbers is
only safe if two appenders racing produce an error rather than a reordering.

Phase 1 fills these (P1-02). They are listed now because the schema's CHECK constraints and
partial indexes are part of the contract, and a repository that quietly works around one has
broken something the database was holding.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import CaseRepository, EventLog

ADAPTERS = [
    pending("sqlite", phase="Phase 1 (card P1-02)"),
]


@pytest.fixture(params=params(ADAPTERS))
def cases(request: pytest.FixtureRequest) -> CaseRepository:
    return cast("CaseRepository", build_or_skip(request.param))


@pytest.fixture(params=params(ADAPTERS))
def events(request: pytest.FixtureRequest) -> EventLog:
    return cast("EventLog", build_or_skip(request.param))


class TestCaseRepository:
    def test_insert_then_get_round_trips(self, cases: CaseRepository) -> None: ...

    def test_a_second_open_case_for_one_pair_is_refused(self, cases: CaseRepository) -> None:
        """The unique partial index makes intake dedupe a database guarantee, not a check
        with a race in it."""

    def test_saving_with_a_stale_version_raises_concurrency_conflict(
        self, cases: CaseRepository
    ) -> None: ...

    def test_a_successful_save_returns_the_bumped_version(self, cases: CaseRepository) -> None: ...

    def test_get_raises_not_found_for_an_unknown_id(self, cases: CaseRepository) -> None: ...

    def test_list_in_states_is_ordered_oldest_first(self, cases: CaseRepository) -> None:
        """Restart recovery works through the backlog, not the newest arrivals."""

    def test_related_excludes_the_case_itself(self, cases: CaseRepository) -> None: ...


class TestEventLog:
    def test_append_assigns_the_next_sequence_number(self, events: EventLog) -> None: ...

    def test_a_repeated_dedupe_key_raises_duplicate_event(self, events: EventLog) -> None: ...

    def test_two_appenders_racing_produce_an_error_not_a_reordering(self, events: EventLog) -> None:
        """UNIQUE(case_id, seq): failing loudly beats a diary that reads out of order."""

    def test_there_is_no_way_to_update_or_delete(self, events: EventLog) -> None:
        """I8. Checked against the port, so adding such a method breaks this test."""
        assert not hasattr(events, "update")
        assert not hasattr(events, "delete")

    def test_list_for_case_is_ordered_by_sequence(self, events: EventLog) -> None: ...
