"""P6-05, I10: with the kill switch on, nothing reaches the outside world.

Proved by counting calls on the fakes - not by reading state afterwards. The executor-level
half (the switch checked inside `plan` and inside `execute`, even when `execute` is called
directly) is tests/unit/executor/test_executor.py; these drills prove it on the whole line.
"""

from __future__ import annotations

from typing import Any

from dispute_agent.kernel.domain.enums import CaseState, ReviewCode


def calls(world: Any) -> tuple[int, int, int]:
    return world.channel._send_calls, world.tickets._create_calls, world.ledger.post_calls


def test_an_approval_given_while_the_switch_is_on_sends_nothing(stage: Any) -> None:
    played = stage(
        "kurta_box_larger",
        ["dispute_raised@0h", "kill_switch(on)@0.5h", "approve(evidence_request)@1h"],
        ["request_seller_evidence"],
    )
    world = played.world
    assert calls(world) == (0, 0, 0)
    case = world.report().case
    assert case.state is CaseState.NEEDS_HUMAN_REVIEW
    assert case.review_reason is not None
    assert case.review_reason.code is ReviewCode.KILL_SWITCH_ACTIVE


def test_a_credit_approved_while_the_switch_is_on_never_reaches_the_ledger(stage: Any) -> None:
    played = stage(
        "calc_error_wrong_slab",
        ["dispute_raised@0h", "kill_switch(on)@0.5h", "approve(credit)@1h"],
        ["propose_credit"],
    )
    assert calls(played.world) == (0, 0, 0)
    assert played.world.ledger.credit_count() == 0


def test_a_remeasurement_allowed_while_the_switch_is_on_creates_no_issue(stage: Any) -> None:
    """An ALLOW needs no approval, so this is the switch inside `plan` with nobody in between."""
    played = stage(
        "ambiguous_image",
        ["dispute_raised@0h"],
        ["request_remeasurement"],
    )
    # Re-run the same case with the switch on from the start.
    switched = stage(
        "ambiguous_image",
        ["kill_switch(on)@0h", "dispute_raised@0h"],
        ["request_remeasurement"],
    )
    assert played.world.tickets._create_calls == 1  # control: without the switch, one issue
    assert calls(switched.world) == (0, 0, 0)
