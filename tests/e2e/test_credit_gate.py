"""I3, in its sharpest form: call the executor directly, skip every station, try to credit.

P5-04's named negative tests, against the real SQLite store - so both layers are under test:
the executor's own binding check, and the database's (`tr_actions_approval_binding`, the
CHECK that a credit row has an approval, and the UNIQUE on `used_by_action_id`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
import sqlalchemy

from dispute_agent.api.agent_trial import _open_case
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import (
    Action,
    ActionIntent,
    ApprovalRequest,
    CreditPayload,
)
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    IntentSource,
    IntentStatus,
    PolicyStage,
)
from dispute_agent.kernel.domain.errors import PolicyRefused
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, IdempotencyKey, IntentId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.executor.executor import ActionExecutor
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_5_rules
from dispute_agent.kernel.policy.verdicts import PolicyContext
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT as SELLER

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)

OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")


class _Off:
    def is_engaged(self) -> bool:
        return False


@pytest.fixture
def world(tmp_path: Path) -> Any:
    engine = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    clock, ids = FakeClock(NOW), FakeIdGenerator()
    facts = CaseFacts(
        shipment_ref="SHP-000001",
        claim_ref="CLM-000001",
        seller_ref="SLR-000001",
        seller_contact=SELLER,
        raised_at=NOW,
    )
    with SqliteUnitOfWork(engine, clock=clock) as work:
        case, _ = _open_case(work, facts, "credit_gate", NOW, ids)
    return engine, clock, ids, case


def credit_intent(case: Any, amount: str = "150.00", intent_id: str = "int_credit") -> ActionIntent:
    payload = CreditPayload(
        shipment_ref="SHP-000001", amount=Money.of(amount), reason_code="calculation_error"
    )
    return ActionIntent(
        id=IntentId(intent_id),
        case_id=case.id,
        source_kind=IntentSource.PROPOSAL,
        source_ref="prp_x",
        action_type=ActionType.POST_CREDIT,
        payload=payload,
        payload_hash=payload_hash(payload.model_dump(mode="json")),
        status=IntentStatus.PENDING_APPROVAL,
        created_at=NOW,
    )


def approve(work: Any, intent: ActionIntent, amount: str = "150.00") -> Any:
    approval_id = ApprovalId(f"apr_{intent.id}")
    work.approvals.add(
        ApprovalRequest(
            approval_id=approval_id,
            case_id=intent.case_id,
            intent_id=intent.id,
            subject=ApprovalSubject.CREDIT,
            payload_hash=intent.payload_hash,
            amount=Money.of(amount),
            requested_at=NOW,
            expires_at=NOW + timedelta(hours=72),
        )
    )
    return work.approvals.decide(approval_id, ApprovalDecision.APPROVED, OWNER, NOW, "")


def plan(work: Any, case: Any, intent: ActionIntent, clock: Any, ids: Any, act: str) -> Any:
    executor = ActionExecutor(PolicyEngine(phase_5_rules(), clock, ids), work.actions, _Off())
    policy = PolicyConfig(messages=MessagesConfig(allowed_recipients=frozenset({SELLER})))
    modules = dict.fromkeys(("email", "ticketing", "ledger", "approvals", "evidence"), True)
    ctx = PolicyContext(
        case=case, stage=PolicyStage.EXECUTION, policy=policy, now=NOW, modules=modules
    )
    return executor.plan(case, intent, ctx, NOW, ActionId(act))


class TestTheExecutorRefuses:
    def test_a_credit_with_no_approval_record_is_refused(self, world: Any) -> None:
        """The phase prompt's named negative test (I3)."""
        engine, clock, ids, case = world
        with SqliteUnitOfWork(engine, clock=clock) as work:
            intent = credit_intent(case)
            work.intents.add(intent)
            with pytest.raises(PolicyRefused):
                plan(work, case, intent, clock, ids, "act_1")
            assert work.actions.for_intent(intent.id) is None

    def test_an_approval_already_used_cannot_be_used_again(self, world: Any) -> None:
        engine, clock, ids, case = world
        with SqliteUnitOfWork(engine, clock=clock) as work:
            intent = credit_intent(case)
            work.intents.add(intent)
            approval = approve(work, intent)
            used = Action(
                id=ActionId("act_first"),
                case_id=case.id,
                intent_id=intent.id,
                approval_id=approval.id,
                action_type=ActionType.POST_CREDIT,
                action_seq=1,
                idempotency_key=IdempotencyKey("wd1:" + "1" * 64),
                payload_hash=intent.payload_hash,
                status=ActionStatus.PLANNED,
                planned_at=NOW,
                credit_shipment_ref="SHP-000001",
                credit_reason_code="calculation_error",
            )
            work.actions.insert(used)
            with pytest.raises(PolicyRefused):
                plan(work, case, intent, clock, ids, "act_second")

    def test_an_approval_for_150_00_cannot_serve_an_intent_of_150_01(self, world: Any) -> None:
        engine, clock, ids, case = world
        with SqliteUnitOfWork(engine, clock=clock) as work:
            intent = credit_intent(case, amount="150.01")
            work.intents.add(intent)
            approve(work, intent, amount="150.00")
            with pytest.raises(PolicyRefused, match="amount"):
                plan(work, case, intent, clock, ids, "act_1")


class TestTheDatabaseRefusesToo:
    """Even with the executor's checks bypassed entirely, no credit row can be written."""

    def test_the_model_refuses_a_credit_action_without_an_approval(self) -> None:
        """A third layer: the domain model will not even represent one."""
        from dispute_agent.kernel.domain.errors import DomainValidationError

        with pytest.raises((DomainValidationError, ValueError)):
            Action(
                id=ActionId("act_rogue"),
                case_id="case_x",
                intent_id=IntentId("int_x"),
                approval_id=None,
                action_type=ActionType.POST_CREDIT,
                action_seq=1,
                idempotency_key=IdempotencyKey("wd1:" + "2" * 64),
                payload_hash=payload_hash("p"),
                status=ActionStatus.PLANNED,
                planned_at=NOW,
                credit_shipment_ref="SHP-000001",
                credit_reason_code="calculation_error",
            )

    def test_a_credit_row_without_an_approval_is_refused_by_the_schema(self, world: Any) -> None:
        """With every line of code bypassed - raw SQL - the database still says no (I3)."""
        from dispute_agent.infra.sqlite.tables import actions

        engine, clock, _ids, case = world
        with (
            pytest.raises(sqlalchemy.exc.IntegrityError),
            SqliteUnitOfWork(engine, clock=clock) as work,
        ):
            intent = credit_intent(case)
            work.intents.add(intent)
            work.connection.execute(
                actions.insert().values(
                    id="act_rogue",
                    case_id=str(case.id),
                    intent_id=str(intent.id),
                    approval_id=None,
                    action_type="POST_CREDIT",
                    action_seq=1,
                    idempotency_key="wd1:" + "2" * 64,
                    payload_hash=str(intent.payload_hash),
                    status="PLANNED",
                    attempts=0,
                    planned_at="2026-11-04T09:00:00.000000Z",
                    credit_shipment_ref="SHP-000001",
                    credit_reason_code="calculation_error",
                )
            )
