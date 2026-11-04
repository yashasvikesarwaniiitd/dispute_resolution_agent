"""The Phase 5 safety review's eight findings, one test (or a few) each.

Each test fails against the code as it was before the fix, and names the finding it covers.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx2 as httpx
import openai
import pytest

from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import Action
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    IntentSource,
    MessageKind,
    ReviewCode,
    ScreenKind,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import BudgetExceeded, GuardrailBlocked, NotFound
from dispute_agent.kernel.domain.ids import ActionId, IdempotencyKey, IntentId
from dispute_agent.kernel.domain.reasoning import ScreenResult
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.executor.executor import ActionExecutor, ExecutionResult, Outcome
from dispute_agent.kernel.orchestrator.execution import ExecuteStep, VerifyStep, _kernel_facts
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_5_rules
from dispute_agent.kernel.policy.rules.model_signal_rules import ManipulationFlagged
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price
from dispute_agent.modules.model_gateway.foundry_openai.gateway import FoundryOpenAiGateway
from tests.e2e.test_credit_gate import NOW, _Off, approve, credit_intent, world
from tests.model_fixtures import request
from tests.unit.gateway.test_failover_matrix import CASE, Route, guard, req
from tests.unit.policy.test_rules import ctx

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["world"]  # the shared fixture, re-exported so pytest finds it here

NONE: Any = None


# -- 1. the claim is saved before the external write ----------------------------------------


class _CrashesMidWrite:
    """A far end that may have accepted the write when the process dies."""

    idempotent_writes = False
    lookup_supported = False

    def send(self, payload: object, key: IdempotencyKey) -> object:
        raise RuntimeError("the process died mid-write")


class TestFinding1TheClaimIsDurable:
    def _crash(self, world: Any, *, checkpointed: bool) -> Any:
        engine, clock, ids, case = world
        with SqliteUnitOfWork(engine, clock=clock) as work:
            intent = credit_intent(case)
            work.intents.add(intent)
            approval = approve(work, intent)
            # Inserted as the planner would leave it; planning itself is tested elsewhere.
            action = work.actions.insert(
                Action(
                    id=ActionId("act_1"),
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
            )
        with pytest.raises(RuntimeError), SqliteUnitOfWork(engine, clock=clock) as work:
            executor = ActionExecutor(
                PolicyEngine(phase_5_rules(), clock, ids), work.actions, _Off()
            )
            executor.execute(
                action.id,
                cast("Any", _CrashesMidWrite()),
                intent.payload,
                NOW,
                checkpoint=work.checkpoint if checkpointed else None,
            )
        return engine, clock, ids, intent, action

    def test_a_crash_mid_write_leaves_the_attempt_on_record(self, world: Any) -> None:
        engine, clock, _ids, _intent, action = self._crash(world, checkpointed=True)
        with SqliteUnitOfWork(engine, clock=clock) as work:
            assert work.actions.get(action.id).attempts == 1

    def test_without_the_save_point_the_attempt_vanishes(self, world: Any) -> None:
        """The bug, kept as a control: the rollback erased the only sign a write may exist."""
        engine, clock, _ids, _intent, action = self._crash(world, checkpointed=False)
        with SqliteUnitOfWork(engine, clock=clock) as work:
            assert work.actions.get(action.id).attempts == 0

    def test_the_recovery_after_the_crash_asks_a_person_instead_of_writing_again(
        self, world: Any
    ) -> None:
        engine, clock, ids, intent, action = self._crash(world, checkpointed=True)
        later = NOW + timedelta(hours=1)  # past the crashed worker's lease
        with SqliteUnitOfWork(engine, clock=clock) as work:
            executor = ActionExecutor(
                PolicyEngine(phase_5_rules(), clock, ids), work.actions, _Off()
            )
            result = executor.execute(
                action.id, cast("Any", _CrashesMidWrite()), intent.payload, later
            )
        assert (result.outcome, result.reason) == (Outcome.UNKNOWN, "recovery_unconfirmed")


# -- 2. R21 escalates on every screen flag that means "not properly checked" -----------------


@pytest.mark.parametrize("flag", ["screen_error", "truncated_input", "fabricated_reference"])
def test_finding2_r21_escalates_on_an_unchecked_text(flag: str) -> None:
    screened = ScreenResult(kind=ScreenKind.SELLER_REPLY, flags=(flag,), adapter="rules_based")
    result = ManipulationFlagged().evaluate(ctx(screen_results=(screened,)))
    assert result.verdict is VerdictKind.ESCALATE
    assert flag in result.reason


# -- 3. a BLOCKED execute is not a failure ---------------------------------------------------


def _execute_step(status: ActionStatus) -> ExecuteStep:
    actions = SimpleNamespace(get=lambda _id: SimpleNamespace(status=status))
    return ExecuteStep(intents=NONE, actions=cast("Any", actions), executor=NONE, ports=NONE)


class TestFinding3BlockedExecution:
    def test_another_worker_holding_the_claim_means_rest(self) -> None:
        outcome = _execute_step(ActionStatus.PLANNED)._blocked(ActionId("a"), "claimed_elsewhere")
        assert outcome.rests

    def test_an_action_already_started_and_unknown_stays_unknown(self) -> None:
        outcome = _execute_step(ActionStatus.UNKNOWN)._blocked(ActionId("a"), "already_started")
        assert outcome.review_reason is not None
        assert outcome.review_reason.code is ReviewCode.ACTION_UNKNOWN

    def test_an_action_already_started_and_failed_is_a_failure(self) -> None:
        outcome = _execute_step(ActionStatus.FAILED)._blocked(ActionId("a"), "already_started")
        assert outcome.review_reason is not None
        assert outcome.review_reason.code is ReviewCode.ACTION_FAILED


# -- 4. a billed failure on the direct route is charged ---------------------------------------

PRICE = Price(input_per_mtok=Decimal("0.25"), output_per_mtok=Decimal("2.00"), source="test")


class _Responses:
    def __init__(self, outcome: Any) -> None:
        self.outcome = outcome

    def create(self, **_: Any) -> Any:
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _direct(outcome: Any) -> FoundryOpenAiGateway:
    client = SimpleNamespace(responses=_Responses(outcome))
    return FoundryOpenAiGateway(deployment="gpt-5-mini", price=PRICE, client=client)


class TestFinding4DirectRouteCharges:
    def test_a_timeout_is_charged_at_the_worst_case(self) -> None:
        timeout = openai.APITimeoutError(request=httpx.Request("POST", "https://x.invalid"))
        with pytest.raises(Exception) as raised:
            _direct(timeout).complete(request())
        usage: Any = getattr(raised.value, "usage", None)
        assert usage is not None
        assert usage.cost_usd > 0

    def test_a_content_filter_stop_carries_its_cost(self) -> None:
        filtered = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="content_filter"),
            usage=SimpleNamespace(input_tokens=500, output_tokens=40),
            output_text="",
            model="gpt-5-mini",
        )
        with pytest.raises(GuardrailBlocked) as raised:
            _direct(filtered).complete(request())
        usage: Any = raised.value.usage
        assert (usage.input_tokens, usage.output_tokens) == (500, 40)
        assert usage.cost_usd == PRICE.cost(500, 40)


# -- 5. the credited amount comes from the verified credit, not the message ------------------


def _confirmation(source_ref: str | None, claimed: str = "999.00") -> Any:
    numbers = (SimpleNamespace(fact_id="credited_amount", text=f"₹{claimed}"),)
    return SimpleNamespace(
        case_id="case_1",
        source_kind=IntentSource.FOLLOW_UP,
        source_ref=source_ref,
        payload=SimpleNamespace(kind=MessageKind.CREDIT_CONFIRMATION, numbers_used=numbers),
    )


def _stores(status: ActionStatus | None, amount: str = "38.00") -> tuple[Any, Any]:
    credit = SimpleNamespace(
        id=IntentId("int_credit"),
        case_id="case_1",
        action_type=ActionType.POST_CREDIT,
        payload=SimpleNamespace(amount=Money.of(amount)),
    )

    def get(intent_id: IntentId) -> Any:
        if intent_id != credit.id:
            raise NotFound("no such intent")
        return credit

    action = None if status is None else SimpleNamespace(status=status)
    return SimpleNamespace(get=get), SimpleNamespace(for_intent=lambda _id: action)


class TestFinding5CreditedAmount:
    def test_the_amount_is_the_verified_credit_s_not_the_message_s(self) -> None:
        intents, actions = _stores(ActionStatus.VERIFIED)
        [fact] = _kernel_facts(_confirmation("int_credit"), intents, actions)
        assert fact.value == Money.of("38.00")

    @pytest.mark.parametrize(
        ("source", "status"),
        [
            pytest.param("int_credit", ActionStatus.SENT, id="credit not verified yet"),
            pytest.param("int_credit", None, id="credit never planned"),
            pytest.param("int_other", ActionStatus.VERIFIED, id="unknown source"),
            pytest.param(None, ActionStatus.VERIFIED, id="no source at all"),
        ],
    )
    def test_without_a_verified_credit_there_is_no_fact(
        self, source: str | None, status: ActionStatus | None
    ) -> None:
        intents, actions = _stores(status)
        assert _kernel_facts(_confirmation(source), intents, actions) == ()


# -- 6. only "not visible yet" rests at verification -----------------------------------------


def _verify_step(reason: str) -> VerifyStep:
    intent = SimpleNamespace(
        id=IntentId("int_1"),
        action_type=ActionType.SEND_MESSAGE,
        payload=SimpleNamespace(kind=MessageKind.EVIDENCE_REQUEST),
    )
    action = SimpleNamespace(
        id=ActionId("act_1"),
        action_type=ActionType.SEND_MESSAGE,
        external_ref="msg_1",
        sent_at=NOW,
        planned_at=NOW,
    )
    executor = SimpleNamespace(
        verify=lambda *a, **k: ExecutionResult(Outcome.BLOCKED, action.id, reason)
    )
    return VerifyStep(
        intents=cast("Any", SimpleNamespace(get=lambda _id: intent)),
        actions=cast("Any", SimpleNamespace(for_intent=lambda _id: action)),
        executor=cast("Any", executor),
        ports=lambda *_: NONE,
        policy=NONE,
        approvals=NONE,
        drafter=NONE,
        ids=NONE,
    )


CASE_WAITING: Any = SimpleNamespace(pending_intent_id=IntentId("int_1"), id="case_1")


class TestFinding6Verification:
    def test_an_action_already_verified_moves_the_case_on(self) -> None:
        outcome = _verify_step("not_sent:VERIFIED").run(CASE_WAITING, NOW)
        assert outcome.trigger is Trigger.ACTION_VERIFIED

    @pytest.mark.parametrize("reason", ["not_sent:FAILED", "not_sent:PLANNED", "not_sent:UNKNOWN"])
    def test_anything_else_not_sent_goes_to_a_person(self, reason: str) -> None:
        outcome = _verify_step(reason).run(CASE_WAITING, NOW)
        assert outcome.review_reason is not None
        assert outcome.review_reason.code is ReviewCode.VERIFICATION_FAILED

    def test_not_visible_yet_still_rests(self) -> None:
        assert _verify_step("not_visible_yet").run(CASE_WAITING, NOW).rests


# -- 7. failover is a second call: M01 is checked again ---------------------------------------


def test_finding7_failover_respects_the_per_case_call_limit(tmp_path: Path) -> None:
    from dispute_agent.kernel.domain.errors import TransientPortError

    primary, secondary = Route("primary", TransientPortError("down")), Route("secondary")
    gateway, ledger = guard(tmp_path, primary, secondary)
    # Five calls already made for this case; the failing primary call is the sixth (the limit).
    for _ in range(5):
        ok = Route("primary")
        gateway.primary = ok
        gateway.complete(req())
    gateway.primary = primary
    with pytest.raises(BudgetExceeded, match="llm_budget_exceeded"):
        gateway.complete(req())
    assert secondary.calls == 0
    assert ledger.calls_for_case(CASE) == 6


# -- 8. the ledger takes its write lock before it reads ---------------------------------------


def test_finding8_every_ledger_read_of_a_post_is_under_the_write_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dispute_agent.kernel.domain.external import CreditRequest
    from dispute_agent.kernel.domain.ids import CaseId

    ledger = SimulatedLedger(tmp_path / "ledger.sqlite3", clock=lambda: NOW)
    ledger.seed_debit("SHP-1", Money.of("100.00"), "freight", "txn_seed")
    seen: list[str] = []
    real_connect = sqlite3.connect

    def traced(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        db: sqlite3.Connection = real_connect(*args, **kwargs)
        db.set_trace_callback(seen.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", traced)
    ledger.post_credit(
        CreditRequest(
            case_id=CaseId("case_1"),
            shipment_ref="SHP-1",
            amount=Money.of("10.00"),
            reason_code="calculation_error",
        ),
        IdempotencyKey("wd1:" + "3" * 64),
    )
    statements = [s.strip().split()[0].upper() for s in seen]
    first_select = statements.index("SELECT")
    assert "BEGIN" in statements[:first_select], statements
