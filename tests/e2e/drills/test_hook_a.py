"""ADR 0035: HOOK A connected - a pattern reaches the briefing and can only add caution (I7).

Request a remeasurement is the one action policy ALLOWs without a person (pilot): it is the
action a pattern must be able to slow down. Approvals exist only for credits and messages, so
with a matching pattern, or a lookup that fails, the case goes to a person instead - more
cautious, never less. With no patterns, nothing changes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.errors import TransientPortError
from dispute_agent.kernel.domain.ids import CaseId, PatternId
from dispute_agent.kernel.domain.patterns import PatternRecord

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def a_pattern() -> PatternRecord:
    return PatternRecord(
        id=PatternId("pat_01"),
        status="UNCONFIRMED",
        summary="Several disputes at the same hub scanner this fortnight.",
        matched_on={"hub": "HUB-1"},
        related_case_ids=(CaseId("case_a"), CaseId("case_b"), CaseId("case_c")),
        first_seen_at=NOW,
        last_seen_at=NOW,
    )


class Matching:
    def patterns_for(self, facts: Any) -> list[PatternRecord]:
        return [a_pattern()]


class Broken:
    def patterns_for(self, facts: Any) -> list[PatternRecord]:
        raise TransientPortError("pattern store unavailable")


def remeasure(stage: Any, provider: Any) -> Any:
    import dispute_agent.api.full_run as full_run
    import dispute_agent.api.scenario_runner as runner

    real = full_run.open_world

    def with_patterns(*args: Any, **kwargs: Any) -> Any:
        world = real(*args, **kwargs)
        world.line.patterns = provider
        return world

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(full_run, "open_world", with_patterns)
        patch.setattr(runner, "open_world", with_patterns, raising=False)
        return stage("ambiguous_image", ["dispute_raised@0h"], ["request_remeasurement"]).world


def state(world: Any) -> CaseState:
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        return work.cases.get(world.case_id).state


def test_without_patterns_the_remeasurement_goes_straight_out(stage: Any) -> None:
    world = remeasure(stage, None)
    assert state(world) is CaseState.AWAITING_OPS
    assert world.tickets.issue_count() == 1


def test_a_matching_pattern_hands_it_to_a_person(stage: Any) -> None:
    world = remeasure(stage, Matching())
    assert state(world) is CaseState.NEEDS_HUMAN_REVIEW
    assert world.tickets.issue_count() == 0


def test_a_pattern_store_that_cannot_be_read_is_caution_not_a_skip(stage: Any) -> None:
    world = remeasure(stage, Broken())
    assert state(world) is CaseState.NEEDS_HUMAN_REVIEW
    assert world.tickets.issue_count() == 0
