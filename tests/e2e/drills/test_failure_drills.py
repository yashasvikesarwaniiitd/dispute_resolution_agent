"""P6-04: failure drills - the outside world misbehaving, and two writers racing on a case.

The named failure scenarios run through the same simulator as the suite (their expectations
are the catalogue's), so a drill and the suite can never disagree about what "passed" means.
The concurrency drill is new: ADR 0008's optimistic versioning under contention.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dispute_agent.api.scenario_runner import play
from dispute_agent.app import build_scenario_models
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.errors import ConcurrencyConflict
from dispute_agent.kernel.orchestrator.dispatch import Dispatcher
from dispute_agent.modules.evals.scenarios.catalogue import load, recorded_actions

CATALOGUE = Path(__file__).resolve().parents[3] / "docs" / "spec" / "eval-scenarios.yaml"
BY_ID = {s.id: s for s in load(CATALOGUE)}

#: The card's list: what each drill is about.
DRILLS = {
    "S12a": "email send timed out after sending: found by lookup, sent once",
    "S12b": "email stuck in Drafts: UNKNOWN, a person resolves, then sent once",
    "S12c": "Jira refused the issue (400): a failed action for a person, no retry",
    "S13a": "ledger timed out after writing: found by lookup, credited once",
    "S13b": "ledger posted the wrong amount: never resolved, never confirmed",
    "S09a": "a late reply inside the reopen window reopens the case",
    "S09b": "a late reply outside the window goes to a person",
    "S43d": "a late reply after an upheld charge, outside the window",
    "S08": "a silent seller: reminder, then closed as no response",
    "S36": "a silent seller whose reminder was refused",
    "S11": "duplicate dispute, duplicate approval, a ledger timeout and a retry",
}


@pytest.mark.parametrize("sid", sorted(DRILLS), ids=lambda sid: f"{sid}-{DRILLS[sid][:30]}")
def test_failure_drill(sid: str) -> None:
    scenario = BY_ID[sid]
    result = play(scenario, recorded_actions(scenario, BY_ID), build_scenario_models)
    assert result.passed, f"{sid}: " + "; ".join(result.mismatches)


class TestConcurrentWritersOnOneCase:
    def test_two_writers_with_the_same_version_one_wins_the_other_is_told(self, stage: Any) -> None:
        world = stage("kurta_box_larger", ["dispute_raised@0h"], ["request_seller_evidence"]).world
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            seen_by_a = work.cases.get(world.case_id)
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            seen_by_b = work.cases.get(world.case_id)
        assert seen_by_a.version == seen_by_b.version
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            work.cases.save(seen_by_a.model_copy(update={"rejection_count": 1}), seen_by_a.version)
        with (
            pytest.raises(ConcurrencyConflict),
            SqliteUnitOfWork(world.engine, clock=world.clock) as work,
        ):
            work.cases.save(seen_by_b.model_copy(update={"rejection_count": 2}), seen_by_b.version)
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            final = work.cases.get(world.case_id)
        assert final.rejection_count == 1  # the winner's write, never a blend
        assert final.version == seen_by_a.version + 1

    def test_a_dispatch_that_loses_the_race_is_retried_cleanly(
        self, stage: Any, monkeypatch: Any
    ) -> None:
        """The loser's event stays PENDING and the next tick handles it - once."""
        played = stage(
            "kurta_box_larger",
            ["dispute_raised@0h", "approve(evidence_request)@1h"],
            ["request_seller_evidence"],
        )
        world = played.world
        real = Dispatcher.dispatch
        lost = {"once": False}

        def racing(self: Any, inbox_id: Any) -> Any:
            if not lost["once"]:
                lost["once"] = True
                raise ConcurrencyConflict(world.case_id, 0)
            return real(self, inbox_id)

        monkeypatch.setattr(Dispatcher, "dispatch", racing)
        result = world.reply("[reply:photo_shows_large_box] here it is", message_id="race-1")
        assert result.reaction.startswith("retry:")
        world.tick()
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            row = work.connection.exec_driver_sql(
                "SELECT status FROM inbox WHERE source_event_id = 'race-1'"
            ).first()
            replies = work.connection.exec_driver_sql(
                "SELECT count(*) FROM messages WHERE external_message_id = 'race-1'"
            ).scalar_one()
        assert row is not None and row[0] == "PROCESSED"
        assert replies == 1  # handled once, not twice
