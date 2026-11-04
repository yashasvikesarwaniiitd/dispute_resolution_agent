"""P6-06: stop the process at each waiting state, start a new one, and carry on.

"Stop" means: everything in memory is dropped - a new Runner and a new line on the same
database. What the outside world holds (the mailbox, Jira, the ledger) is kept, because a
restart of this process does not restart them. Timers are rows, so nothing should be lost;
recovery runs twice to prove it is idempotent; and no side effect may happen twice.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from typing import Any

import pytest

from dispute_agent.api.runner import Runner
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import CaseState, ReviewCode

S = CaseState
HOURS = timedelta(hours=1)


def restart(world: Any) -> Runner:
    """A new process: a fresh line (no in-memory state) over the same database and fakes."""
    fresh = dataclasses.replace(world.line)
    runner = Runner(world.engine, fresh)
    runner.recover()
    runner.recover()  # idempotent: a crash during recovery is followed by another one
    return runner


def side_effects(world: Any) -> tuple[int, int, int]:
    return world.channel.sent_count(), world.tickets.issue_count(), world.ledger.credit_count()


def state(world: Any) -> CaseState:
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        return work.cases.get(world.case_id).state


def test_awaiting_seller_the_reminder_still_comes_once(stage: Any) -> None:
    world = stage(
        "kurta_box_larger",
        ["dispute_raised@0h", "approve(evidence_request)@1h"],
        ["request_seller_evidence"],
    ).world
    assert state(world) is S.AWAITING_SELLER
    before = side_effects(world)
    world.clock.advance(49 * HOURS)  # the reminder fell due while the process was down
    runner = restart(world)
    runner.tick()
    assert state(world) is S.AWAITING_APPROVAL  # the reminder awaits review, once
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        kinds = [
            a.request.message_kind.value
            for a in work.approvals.for_case(world.case_id)
            if a.request.message_kind is not None
        ]
    assert kinds.count("reminder") == 1
    assert side_effects(world) == before


def test_awaiting_approval_a_decision_after_the_restart_is_acted_on_once(stage: Any) -> None:
    played = stage("calc_error_wrong_slab", ["dispute_raised@0h"], ["propose_credit"])
    world = played.world
    assert state(world) is S.AWAITING_APPROVAL
    restart(world)
    from dispute_agent.api.scenario_runner import _decide
    from dispute_agent.modules.evals.scenarios.catalogue import Step

    _decide(played, Step("approve", "credit", timedelta(0)), "credit", True, "")
    world.drive()
    assert world.ledger.credit_count() == 1
    assert state(world) is S.AWAITING_APPROVAL  # now the confirmation waits


def test_awaiting_ops_the_timeout_still_fires(stage: Any) -> None:
    world = stage("ambiguous_image", ["dispute_raised@0h"], ["request_remeasurement"]).world
    assert state(world) is S.AWAITING_OPS
    world.clock.advance(121 * HOURS)
    restart(world).tick()
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        case = work.cases.get(world.case_id)
    assert case.state is S.NEEDS_HUMAN_REVIEW
    assert case.review_reason is not None and case.review_reason.code is ReviewCode.OPS_TIMEOUT
    assert world.tickets.issue_count() == 1


def test_resting_in_gathering_the_evidence_retry_still_runs(stage: Any) -> None:
    world = stage(
        "kurta_box_larger",
        ["dispute_raised@0h"],
        ["request_seller_evidence"],
        faults={"evidence.measurements": "timeout_twice"},
    ).world
    assert state(world) is S.GATHERING_EVIDENCE  # resting, a retry armed
    for _ in range(2):
        world.clock.advance(timedelta(minutes=16))
        restart(world).tick()
    assert state(world) is S.AWAITING_APPROVAL  # the round completed and was investigated


def test_resting_in_verifying_the_read_back_completes_without_a_second_send(
    stage: Any,
) -> None:
    played = stage("kurta_box_larger", ["dispute_raised@0h"], ["request_seller_evidence"])
    world = played.world
    from dispute_agent.api.full_run import REVIEWER
    from dispute_agent.kernel.domain.enums import ApprovalDecision

    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        [pending] = work.approvals.pending(world.case_id)
        work.approvals.decide(
            pending.id, ApprovalDecision.APPROVED, REVIEWER, world.clock.now(), ""
        )
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        case = work.cases.get(world.case_id)
        cause = work.events.list_for_case(world.case_id)[-1].id
        world.line.advance(work, case, cause)  # sent; not yet visible in Sent Items
    assert state(world) is S.VERIFYING
    assert world.channel.sent_count() == 1
    world.clock.advance(timedelta(seconds=30))
    restart(world).tick()
    assert state(world) is S.AWAITING_SELLER
    assert world.channel.sent_count() == 1  # verified, never sent again


@pytest.mark.parametrize(
    "waiting",
    [S.AWAITING_SELLER, S.AWAITING_OPS, S.AWAITING_APPROVAL, S.GATHERING_EVIDENCE, S.VERIFYING],
)
def test_every_waiting_state_has_a_restart_drill(waiting: CaseState) -> None:
    """The card's list, kept honest: each state above has a drill in this file."""
    names = {
        S.AWAITING_SELLER: test_awaiting_seller_the_reminder_still_comes_once,
        S.AWAITING_OPS: test_awaiting_ops_the_timeout_still_fires,
        S.AWAITING_APPROVAL: test_awaiting_approval_a_decision_after_the_restart_is_acted_on_once,
        S.GATHERING_EVIDENCE: test_resting_in_gathering_the_evidence_retry_still_runs,
        S.VERIFYING: test_resting_in_verifying_the_read_back_completes_without_a_second_send,
    }
    assert callable(names[waiting])
