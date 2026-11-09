"""ADR 0050: a person decides a case that is with them, on the whole line.

Each case with a person in the demo set is decided through the case desk, as the Review screen
does, and driven by the real runner to where the decision leads. The decision is a proposal by
a person: the amount is code's, the message is the pack's draft, and a credit or a message is
approved before it goes anywhere (T49), exactly like the agent's.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock, SystemClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    CaseState,
    InternalEventType,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.ui.desk import CaseDesk

if TYPE_CHECKING:
    from dispute_agent.api.full_run import World

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")
ROOT = Path(__file__).resolve().parents[2]


class _Approver:
    """Approves nothing until the person's decision is in, then everything (the same person)."""

    def __init__(self) -> None:
        self.on = False

    def __call__(self, _approval: object) -> ApprovalDecision | None:
        return ApprovalDecision.APPROVED if self.on else None


def _with_a_person(name: str) -> tuple[World, _Approver]:
    approver = _Approver()
    world = open_world(
        name, policy=PolicyConfig(), clock=FakeClock(NOW), ids=FakeIdGenerator(), decide=approver
    )
    world.drive(40)
    assert world.report().case.state is CaseState.NEEDS_HUMAN_REVIEW
    return world, approver


def _decide(world: World, action: str, **kw: Any) -> dict[str, Any]:
    """Decide through the desk, let the runner apply it, and say what is waiting."""
    with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
        CaseDesk(inbox=work.inbox, clock=world.clock, reviewer=OWNER).decide(
            work.cases.get(world.case_id), action, "Checked the photo and the figures.", **kw
        )
    world.clock.advance(timedelta(seconds=5))
    world.tick()
    with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
        return {
            "state": work.cases.get(world.case_id).state,
            "pending": [
                (a.request.subject.value, a.request.amount)
                for a in work.approvals.pending(world.case_id)
            ],
            "refused": [
                e
                for e in work.events.list_for_case(world.case_id)
                if e.type is InternalEventType.HUMAN_DECISION_REFUSED
            ],
            "proposals": list(work.proposals.for_case(world.case_id)),
        }


def _finish(world: World, approver: _Approver) -> CaseState:
    approver.on = True
    world.drive(40)
    return world.report().case.state


@pytest.mark.parametrize(
    "scenario", ["kurta_box_larger", "conflicting_history", "injection_in_statement"]
)
def test_upholding_sends_the_explanation_after_approval_and_closes_upheld(scenario: str) -> None:
    world, approver = _with_a_person(scenario)
    try:
        after = _decide(world, "uphold_charge")
        assert after["state"] is CaseState.AWAITING_APPROVAL  # T49: the email waits for you
        assert [s for s, _ in after["pending"]] == ["MESSAGE"]
        decided = after["proposals"][-1]
        assert decided.reasoner == "human" and decided.decided_by == "human:owner"
        assert decided.prompt_hash is None  # a person's decision has no prompt
        assert _finish(world, approver) is CaseState.RESOLVED_UPHELD
    finally:
        world.close()


def test_asking_the_hub_to_remeasure_raises_a_ticket_and_waits_for_operations() -> None:
    world, approver = _with_a_person("kurta_box_larger")
    try:
        assert _decide(world, "request_remeasurement")["state"] in (
            CaseState.EXECUTING,
            CaseState.VERIFYING,
            CaseState.AWAITING_OPS,
        )
        assert _finish(world, approver) is CaseState.AWAITING_OPS
    finally:
        world.close()


def test_asking_the_seller_sends_the_chosen_items_after_approval() -> None:
    world, approver = _with_a_person("kurta_box_larger")
    try:
        after = _decide(
            world,
            "request_seller_evidence",
            requested_items=("Photo of the packed box beside a tape measure",),
        )
        assert [s for s, _ in after["pending"]] == ["MESSAGE"]
        assert _finish(world, approver) is CaseState.AWAITING_SELLER
    finally:
        world.close()


@pytest.mark.parametrize(
    ("reason", "amount"), [("calculation_error", "1820.00"), ("duplicate_charge", "2800.00")]
)
def test_a_person_chooses_between_two_proven_errors_and_code_sets_the_amount(
    reason: str, amount: str
) -> None:
    """R14 escalates two proven errors to a person; the person's choice is that call."""
    world, approver = _with_a_person("multi_error")
    try:
        after = _decide(world, "propose_credit", reason_code=reason)
        assert after["state"] is CaseState.AWAITING_APPROVAL
        [(subject, money)] = after["pending"]
        assert subject == "CREDIT" and str(money.amount) == amount  # code's figure (I9)
        assert _finish(world, approver) is CaseState.RESOLVED_CREDITED
    finally:
        world.close()


def test_a_credit_code_did_not_prove_is_refused_and_the_case_stays_with_the_person() -> None:
    world, _ = _with_a_person("kurta_box_larger")
    try:
        after = _decide(world, "propose_credit", reason_code="calculation_error")
        assert after["state"] is CaseState.NEEDS_HUMAN_REVIEW
        assert after["pending"] == []
        [refused] = after["refused"]
        assert "credit_reason_unproven" in refused.payload.detail
    finally:
        world.close()


def test_a_decision_from_someone_who_is_not_a_person_is_ignored() -> None:
    """P3: the event is only a person's to send; the runner does not act on a forged one."""
    from dispute_agent.kernel.domain.enums import InboundEventType
    from dispute_agent.kernel.domain.events import InboundEvent
    from dispute_agent.kernel.domain.inbound import HumanDecidedCase

    world, _ = _with_a_person("kurta_box_larger")
    try:
        with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
            case = work.cases.get(world.case_id)
            work.inbox.accept(
                InboundEvent(
                    type=InboundEventType.HUMAN_DECIDED_CASE,
                    source="ops_console",  # the desk's own channel, with no person behind it
                    source_event_id="desk-forged-1",
                    occurred_at=NOW,
                    actor=Actor(kind=ActorKind.SYSTEM, id="system:agent"),
                    case_id=case.id,
                    correlation_id=case.correlation_id,
                    payload=HumanDecidedCase(action="uphold_charge", note="forged"),
                ),
                NOW,
            )
        world.tick()
        with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
            assert work.cases.get(world.case_id).state is CaseState.NEEDS_HUMAN_REVIEW
            assert not [p for p in work.proposals.for_case(world.case_id) if p.reasoner == "human"]
    finally:
        world.close()


def test_the_desk_needs_a_note_and_the_items_to_ask_for() -> None:
    from dispute_agent.kernel.domain.errors import DomainValidationError

    world, _ = _with_a_person("kurta_box_larger")
    try:
        with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
            desk = CaseDesk(inbox=work.inbox, clock=world.clock, reviewer=OWNER)
            case = work.cases.get(world.case_id)
            with pytest.raises(DomainValidationError, match="note"):
                desk.decide(case, "uphold_charge", "  ")
            with pytest.raises(DomainValidationError, match="error it is for"):
                desk.decide(case, "propose_credit", "why")
            with pytest.raises(DomainValidationError, match="item"):
                desk.decide(case, "request_seller_evidence", "why")
    finally:
        world.close()


def test_migration_0003_widens_an_existing_database_and_nothing_else() -> None:
    """An old database refuses the new event; after 0003 it takes it, and still refuses junk."""
    sys.modules.setdefault("alembic.op", types.ModuleType("alembic.op"))
    spec = importlib.util.spec_from_file_location(
        "m0003", ROOT / "migrations" / "versions" / "0003_human_verdict.py"
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    old = (ROOT / "docs" / "spec" / "schema.sql").read_text(encoding="utf-8")
    for _table, before, after, _added in migration.WIDENED:
        old = old.replace(
            after.replace(
                "','HUMAN_DECIDED_CASE'", "',\n                     'HUMAN_DECIDED_CASE'"
            ),
            before,
        )
        old = old.replace(after, before)
    assert "HUMAN_DECIDED_CASE" not in old.split("CREATE TABLE inbox")[1].split(";")[0]

    conn = sqlite3.connect(":memory:")
    conn.executescript(old)
    insert = (
        "INSERT INTO inbox (id, dedupe_key, source, source_event_id, event_type, event_json, "
        "received_at, status) VALUES ('inb_X', 'a:1', 'a', '1', ?, '{}', "
        "'2026-11-10T00:00:00.000000Z', 'PENDING')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("HUMAN_DECIDED_CASE",))
    assert migration.widen(conn) == 2
    conn.commit()  # as alembic does at the end of the migration
    conn.execute(insert, ("HUMAN_DECIDED_CASE",))
    conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert, ("NOT_AN_EVENT",))
    assert migration.widen(conn) == 0  # idempotent


def test_agent_steps_show_the_persons_decision_and_keep_the_agents_proposals_in_place() -> None:
    from dispute_agent.ui.trail import build

    world, _ = _with_a_person("multi_error")
    try:
        _decide(world, "propose_credit", reason_code="duplicate_charge")
        with SqliteUnitOfWork(world.engine, clock=SystemClock()) as work:
            steps = build(work, work.cases.get(world.case_id)).steps
        titles = [s.title for s in steps]
        assert "A person decided: Credit the seller" in titles
        decided = steps[titles.index("A person decided: Credit the seller")]
        assert decided.quoted == ("Checked the photo and the figures.",)
        assert decided.quote_caption.endswith("by owner")
        # The rules engine's proposal still sits on its own investigation step.
        proposed = next(s for s in steps if s.title.startswith("Proposed:"))
        assert proposed.proposer.startswith("Rules engine")
    finally:
        world.close()
