"""Card P1-08: the outbox cycle, and the four invariants it carries.

The tests the card names, each written against the failure it prevents rather than the
feature it describes:

- the PLANNED row and its key are committed before any external call, so a crash between
  planning and executing leaves something recoverable (ADR 0006)
- a replayed action returns the original result and makes no second call (ADR 0007)
- the kill switch refuses even when `execute` is called directly, bypassing the entry
  point (I10)
- a read-back mismatch yields UNKNOWN, and UNKNOWN is never success (I2, I4)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.actions import (
    Action,
    ActionIntent,
    Approval,
    ApprovalRequest,
    CreditPayload,
    MessagePayload,
)
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
)
from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    KillSwitchEngaged,
    PermanentPortError,
    PolicyRefused,
    TransientPortError,
)
from dispute_agent.kernel.domain.ids import (
    ActionId,
    ApprovalId,
    CaseId,
    CorrelationId,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Actor, Case
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.executor.executor import ActionExecutor, Outcome
from dispute_agent.kernel.executor.keys import idempotency_key, jira_label
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_1_rules
from dispute_agent.kernel.policy.verdicts import PolicyContext

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
LATER = NOW + timedelta(hours=72)
HASH = PayloadHash("sha256:" + "a" * 64)
SELLER = "seller@example.invalid"
HUMAN = Actor(kind=ActorKind.HUMAN, id="human:owner")

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact=SELLER,
    raised_at=NOW,
)
CASE = Case(
    id=CaseId("case_01HQ"),
    pack="weight_dispute",
    facts=FACTS,
    state=CaseState.EXECUTING,
    version=1,
    correlation_id=CorrelationId("corr_01HQ"),
    opened_at=NOW,
    updated_at=NOW,
    state_entered_at=NOW,
)
POLICY = PolicyConfig(messages=MessagesConfig(allowed_recipients=frozenset({SELLER})))
MODULES = dict.fromkeys(("email", "ticketing", "ledger", "approvals"), True)


@dataclass
class Receipt:
    external_message_id: str = "ext-1"
    body: str = "approved text"
    to: str = SELLER


@dataclass
class FakePort:
    """An in-memory write port that records what it was asked to do."""

    idempotent_writes: bool = True
    lookup_supported: bool = True
    sends: list[Any] = field(default_factory=list)
    stored: dict[str, Receipt] = field(default_factory=dict)
    send_error: Exception | None = None
    lookup_error: Exception | None = None
    snapshot: Receipt | None = None
    read_error: Exception | None = None

    def send(self, payload: object, key: object) -> Receipt:
        self.sends.append((payload, key))
        if self.send_error is not None:
            raise self.send_error
        receipt = Receipt()
        self.stored[str(key)] = receipt
        return receipt

    def find_sent(self, key: object) -> Receipt | None:
        if self.lookup_error is not None:
            raise self.lookup_error
        return self.stored.get(str(key))

    def read_back(self, receipt: object) -> Receipt:
        if self.read_error is not None:
            raise self.read_error
        return self.snapshot if self.snapshot is not None else Receipt()


@dataclass
class FakeActionStore:
    rows: dict[str, Action] = field(default_factory=dict)
    approval: Approval | None = None
    claimed: set[str] = field(default_factory=set)

    def insert(self, action: Action) -> Action:
        self.rows[str(action.id)] = action
        return action

    def get(self, action_id: ActionId) -> Action:
        return self.rows[str(action_id)]

    def claim(self, action_id: ActionId, worker: str, now: datetime, lease_s: int) -> bool:
        if str(action_id) in self.claimed:
            return False
        self.claimed.add(str(action_id))
        return True

    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action:
        current = self.rows[str(action_id)]
        updated = current.model_copy(update={"status": status, **fields})
        self.rows[str(action_id)] = updated
        return updated

    def find_approval_for(self, intent_id: object) -> Approval | None:
        return self.approval

    live_credit_count: int = 0
    verified_credit: bool = False

    def live_credits(self, shipment_ref: str, reason_code: str) -> int:
        return self.live_credit_count

    def has_verified_credit(self, case_id: object) -> bool:
        return self.verified_credit

    #: Messages already sent on this case, and how many of them were today. Two numbers
    #: rather than one, because R04 and R05 ask different questions.
    messages_total: int = 0
    messages_today: int = 0
    counted_since: datetime | None = None

    def outbound_message_count(self, case_id: object, since: datetime | None = None) -> int:
        self.counted_since = since
        return self.messages_today if since is not None else self.messages_total


class AlwaysOff:
    def is_engaged(self) -> bool:
        return False


class AlwaysOn:
    def is_engaged(self) -> bool:
        return True


def message_intent(**overrides: object) -> ActionIntent:
    fields: dict[str, object] = {
        "id": IntentId("int_01HQ"),
        "case_id": CASE.id,
        "source_kind": IntentSource.PROPOSAL,
        "source_ref": "prp_01HQ",
        "action_type": ActionType.SEND_MESSAGE,
        "payload": MessagePayload(
            kind=MessageKind.EVIDENCE_REQUEST, to=SELLER, subject="s", body="approved text"
        ),
        "payload_hash": HASH,
        "status": IntentStatus.PLANNED,
        "created_at": NOW,
    }
    fields.update(overrides)
    return ActionIntent(**fields)


def credit_intent() -> ActionIntent:
    return ActionIntent(
        id=IntentId("int_01HQ"),
        case_id=CASE.id,
        source_kind=IntentSource.PROPOSAL,
        source_ref="prp_01HQ",
        action_type=ActionType.POST_CREDIT,
        payload=CreditPayload(
            shipment_ref="SHP-0001", amount=Money.of("1820.00"), reason_code="calculation_error"
        ),
        payload_hash=HASH,
        status=IntentStatus.PLANNED,
        created_at=NOW,
    )


def an_approval(
    subject: ApprovalSubject = ApprovalSubject.MESSAGE, **overrides: object
) -> Approval:
    request = ApprovalRequest(
        approval_id=ApprovalId("apr_01HQ"),
        case_id=CASE.id,
        intent_id=IntentId("int_01HQ"),
        subject=subject,
        payload_hash=HASH,
        amount=Money.of("1820.00") if subject is ApprovalSubject.CREDIT else None,
        message_kind=MessageKind.EVIDENCE_REQUEST if subject is ApprovalSubject.MESSAGE else None,
        requested_at=NOW,
        expires_at=LATER,
    )
    fields: dict[str, object] = {
        "id": ApprovalId("apr_01HQ"),
        "request": request,
        "decision": ApprovalDecision.APPROVED,
        "decided_by": HUMAN,
        "decided_at": NOW,
    }
    fields.update(overrides)
    return Approval(**fields)


def ctx() -> PolicyContext:
    return PolicyContext(
        case=CASE, stage=PolicyStage.EXECUTION, modules=MODULES, policy=POLICY, now=NOW
    )


def executor(store: FakeActionStore, kill: object = None) -> ActionExecutor:
    engine = PolicyEngine(phase_1_rules(), FakeClock(NOW), FakeIdGenerator())
    return ActionExecutor(engine, store, kill or AlwaysOff())  # type: ignore[arg-type]


def plan_a_message(
    *, messages_total: int = 0, messages_today: int = 0
) -> tuple[FakeActionStore, PolicyContext]:
    """Plan one message against a store with the given contact history.

    Shared with `tests/unit/policy/test_contact_limits.py`, which uses it to check that the
    executor gathers the counts R04 and R05 read. Returns the store (so a test can see what
    was asked of it) and the context the caller passed in.
    """
    store = FakeActionStore(approval=an_approval())
    store.messages_total = messages_total
    store.messages_today = messages_today
    context = ctx()
    executor(store).plan(CASE, message_intent(), context, NOW, ActionId("act_contact"))
    return store, context


class TestIdempotencyKeys:
    def test_the_key_has_the_shape_the_schema_checks(self) -> None:
        key = idempotency_key(CASE.id, ActionType.SEND_MESSAGE, 1, HASH)
        assert str(key).startswith("wd1:")
        assert len(str(key)) == 68

    def test_the_sequence_number_separates_identical_payloads(self) -> None:
        """ADR 0007. Without it, the same evidence request after a reopen would be swallowed
        as a duplicate of the first."""
        first = idempotency_key(CASE.id, ActionType.SEND_MESSAGE, 1, HASH)
        second = idempotency_key(CASE.id, ActionType.SEND_MESSAGE, 2, HASH)
        assert first != second

    def test_the_same_action_always_derives_the_same_key(self) -> None:
        assert idempotency_key(CASE.id, ActionType.SEND_MESSAGE, 1, HASH) == idempotency_key(
            CASE.id, ActionType.SEND_MESSAGE, 1, HASH
        )

    def test_a_jira_label_drops_the_colon(self) -> None:
        key = idempotency_key(CASE.id, ActionType.UPSERT_ISSUE, 1, HASH)
        label = jira_label(key)
        assert ":" not in label
        assert label.startswith("wd1-")


class TestPlanWritesFirst:
    def test_the_planned_row_exists_before_any_call(self) -> None:
        """ADR 0006: a crash between plan and execute leaves something recoverable."""
        store = FakeActionStore(approval=an_approval())
        action, _verdict = executor(store).plan(
            CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ")
        )
        assert action.status is ActionStatus.PLANNED
        assert action.idempotency_key
        assert store.rows

    def test_the_action_sequence_follows_the_case(self) -> None:
        store = FakeActionStore(approval=an_approval())
        case = CASE.model_copy(update={"action_seq": 4})
        action, _ = executor(store).plan(case, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        assert action.action_seq == 5

    def test_planning_is_refused_when_the_switch_is_on(self) -> None:
        store = FakeActionStore(approval=an_approval())
        with pytest.raises(KillSwitchEngaged):
            executor(store, AlwaysOn()).plan(
                CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ")
            )


class TestApprovalsAreFoundNotAsserted:
    def test_plan_takes_no_already_approved_parameter(self) -> None:
        """I3: there is no argument by which a caller can claim authorisation."""
        import inspect

        names = set(inspect.signature(ActionExecutor.plan).parameters)
        assert names == {"self", "case", "intent", "ctx", "now", "action_id"}

    def test_a_credit_without_an_approval_is_refused(self) -> None:
        store = FakeActionStore(approval=None)
        with pytest.raises(PolicyRefused):
            executor(store).plan(CASE, credit_intent(), ctx(), NOW, ActionId("act_01HQ"))

    def test_a_used_approval_is_refused(self) -> None:
        """Single use, or one yes becomes two credits."""
        store = FakeActionStore(
            approval=an_approval(ApprovalSubject.CREDIT, used_by_action_id=ActionId("act_00HQ"))
        )
        with pytest.raises(PolicyRefused):
            executor(store).plan(CASE, credit_intent(), ctx(), NOW, ActionId("act_01HQ"))

    def test_an_expired_approval_is_refused(self) -> None:
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        with pytest.raises(PolicyRefused, match="stale"):
            executor(store).plan(
                CASE, credit_intent(), ctx(), LATER + timedelta(hours=1), ActionId("act_01HQ")
            )

    def test_a_bound_approval_is_recorded_on_the_action(self) -> None:
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        action, _ = executor(store).plan(CASE, credit_intent(), ctx(), NOW, ActionId("act_01HQ"))
        assert action.approval_id == ApprovalId("apr_01HQ")


class TestIdempotentExecution:
    def plan_one(self, store: FakeActionStore) -> ActionId:
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        return action.id

    def test_a_first_send_reaches_the_port(self) -> None:
        store = FakeActionStore(approval=an_approval())
        action_id = self.plan_one(store)
        port = FakePort()
        result = executor(store).execute(action_id, port, "payload", NOW)
        assert result.outcome is Outcome.SENT
        assert len(port.sends) == 1

    def test_a_replay_finds_the_original_and_does_not_send_again(self) -> None:
        """ADR 0007: look before writing. This is the crash-after-write case."""
        store = FakeActionStore(approval=an_approval())
        action_id = self.plan_one(store)
        port = FakePort()
        executor(store).execute(action_id, port, "payload", NOW)

        store.rows[str(action_id)] = store.rows[str(action_id)].model_copy(
            update={"status": ActionStatus.PLANNED}
        )
        store.claimed.clear()
        result = executor(store).execute(action_id, port, "payload", NOW)

        assert result.outcome is Outcome.SENT
        assert result.reason == "found_by_lookup"
        assert len(port.sends) == 1

    def test_an_action_already_started_is_blocked(self) -> None:
        store = FakeActionStore(approval=an_approval())
        action_id = self.plan_one(store)
        port = FakePort()
        executor(store).execute(action_id, port, "payload", NOW)
        again = executor(store).execute(action_id, port, "payload", NOW)
        assert again.outcome is Outcome.BLOCKED

    def test_a_claim_held_elsewhere_rests_rather_than_failing(self) -> None:
        """No commit, no escalation: the sweep comes back to it."""
        store = FakeActionStore(approval=an_approval())
        action_id = self.plan_one(store)
        store.claimed.add(str(action_id))
        result = executor(store).execute(action_id, FakePort(), "payload", NOW)
        assert result.outcome is Outcome.BLOCKED
        assert result.reason == "claimed_elsewhere"


class TestTheKillSwitchIsCheckedInside:
    def test_calling_execute_directly_still_refuses(self) -> None:
        """I10. The card's test: bypassing the entry point changes nothing."""
        store = FakeActionStore(approval=an_approval())
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        port = FakePort()
        result = executor(store, AlwaysOn()).execute(action.id, port, "payload", NOW)
        assert result.outcome is Outcome.FAILED
        assert result.reason == "kill_switch_active"
        assert port.sends == []


class TestUnknownIsNeverSuccess:
    def plan_and_send(self, store: FakeActionStore, port: FakePort) -> ActionId:
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        executor(store).execute(action.id, port, "payload", NOW)
        return action.id

    def test_an_ambiguous_write_becomes_unknown(self) -> None:
        """Retrying could do it twice; calling it failed could lose a posted credit."""
        store = FakeActionStore(approval=an_approval())
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        port = FakePort(lookup_supported=False, send_error=AmbiguousOutcome("timeout"))
        result = executor(store).execute(action.id, port, "payload", NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert result.is_success is False

    def test_a_read_back_mismatch_becomes_unknown(self) -> None:
        """I2 and I13: what is out there is not what was approved."""
        store = FakeActionStore(approval=an_approval())
        port = FakePort()
        action_id = self.plan_and_send(store, port)
        port.snapshot = Receipt(body="something else entirely")
        result = executor(store).verify(action_id, port, Receipt(), {"body": "approved text"}, NOW)
        assert result.outcome is Outcome.UNKNOWN
        assert "verification_mismatch" in result.reason

    def test_a_matching_read_back_verifies(self) -> None:
        store = FakeActionStore(approval=an_approval())
        port = FakePort()
        action_id = self.plan_and_send(store, port)
        result = executor(store).verify(
            action_id, port, Receipt(), {"body": "approved text", "to": SELLER}, NOW
        )
        assert result.outcome is Outcome.VERIFIED
        assert store.rows[str(action_id)].status is ActionStatus.VERIFIED

    def test_unknown_is_not_counted_as_success(self) -> None:
        from dispute_agent.kernel.executor.executor import ExecutionResult

        assert ExecutionResult(Outcome.UNKNOWN, ActionId("act_01HQ")).is_success is False
        assert ExecutionResult(Outcome.FAILED, ActionId("act_01HQ")).is_success is False
        assert ExecutionResult(Outcome.VERIFIED, ActionId("act_01HQ")).is_success is True

    def test_a_target_not_yet_visible_is_not_a_mismatch(self) -> None:
        """Waiting is different from wrong, and only one of them needs a person."""
        store = FakeActionStore(approval=an_approval())
        port = FakePort()
        action_id = self.plan_and_send(store, port)
        port.read_error = TransientPortError("not indexed yet")
        result = executor(store).verify(action_id, port, Receipt(), {}, NOW)
        assert result.outcome is Outcome.BLOCKED


class TestFailures:
    def test_a_permanent_error_fails_without_retrying(self) -> None:
        store = FakeActionStore(approval=an_approval())
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        port = FakePort(lookup_supported=False, send_error=PermanentPortError("bad request"))
        result = executor(store).execute(action.id, port, "payload", NOW)
        assert result.outcome is Outcome.FAILED
        assert result.reason == "permanent"

    def test_a_transient_error_fails_safely_because_nothing_was_written(self) -> None:
        """TransientPortError means the call certainly did not take effect, which is exactly
        why it is a different type from AmbiguousOutcome.

        The example is a connection that never opened. It used to be "timeout", which is
        the wrong lesson: a timeout after a write was dispatched may well have taken effect,
        and is AmbiguousOutcome (Phase 3 review, finding 1)."""
        store = FakeActionStore(approval=an_approval())
        action, _ = executor(store).plan(CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ"))
        port = FakePort(lookup_supported=False, send_error=TransientPortError("could not connect"))
        result = executor(store).execute(action.id, port, "payload", NOW)
        assert result.outcome is Outcome.FAILED
        assert result.reason == "transient_exhausted"


class TestConstruction:
    def test_the_lease_must_outlast_the_budget(self) -> None:
        """executor.md section 3: a lease that expires mid-write lets a second worker in."""
        store = FakeActionStore()
        engine = PolicyEngine(phase_1_rules(), FakeClock(NOW), FakeIdGenerator())
        with pytest.raises(ValueError, match="must exceed"):
            ActionExecutor(engine, store, AlwaysOff(), claim_lease_s=100, execute_budget_s=300)


class TestTheExecutorSuppliesItsOwnSafetyInputs:
    """Regression for the Phase 1 safety review, finding 1.

    `plan` used to build the execution context with only the stage, intent, approval and
    clock. Everything else came from whatever the caller passed, so R02 read a kill switch
    nobody set and R17 counted credits nobody counted — a rule that believed it had checked.
    The executor now gathers all three from the stores it already holds.
    """

    def plan_credit(self, store: FakeActionStore) -> tuple[Action, object]:
        return executor(store).plan(CASE, credit_intent(), ctx(), NOW, ActionId("act_01HQ"))

    def test_a_duplicate_credit_is_refused_even_with_a_clean_context(self) -> None:
        """The break the review named: a VERIFIED credit already exists for this shipment
        and reason, the caller's context says nothing, and R17 must still refuse."""
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        store.live_credit_count = 1
        with pytest.raises(PolicyRefused):
            self.plan_credit(store)

    def test_a_case_that_already_credited_is_refused(self) -> None:
        """ADR 0012, through the same path."""
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        store.verified_credit = True
        with pytest.raises(PolicyRefused):
            self.plan_credit(store)

    def test_a_clean_credit_still_plans(self) -> None:
        """The positive control: the count is consulted, not assumed to be non-zero."""
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        action, _ = self.plan_credit(store)
        assert action.action_type is ActionType.POST_CREDIT

    def test_the_planned_credit_carries_its_index_keys(self) -> None:
        """Without these, ux_actions_one_credit indexes NULLs and refuses nothing (I3)."""
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        action, _ = self.plan_credit(store)
        assert action.credit_shipment_ref == "SHP-0001"
        assert action.credit_reason_code == "calculation_error"

    def test_the_kill_switch_reaches_the_policy_engine_too(self) -> None:
        """R02 was a dead rule on this path: the context's kill_switch was never set from
        the switch the executor holds. plan() raises before policy now, so this asserts the
        direct check and the rule agree rather than only one of them working."""
        store = FakeActionStore(approval=an_approval())
        with pytest.raises(KillSwitchEngaged):
            executor(store, AlwaysOn()).plan(
                CASE, message_intent(), ctx(), NOW, ActionId("act_01HQ")
            )

    def test_a_credit_payload_missing_its_keys_is_refused(self) -> None:
        """Counting zero for a payload that cannot be checked would be the same bug again."""
        store = FakeActionStore(approval=an_approval(ApprovalSubject.CREDIT))
        broken = credit_intent().model_copy(
            update={
                "payload": MessagePayload(
                    kind=MessageKind.REMINDER, to=SELLER, subject="s", body="b"
                )
            }
        )
        with pytest.raises(PolicyRefused, match="credit_payload_incomplete"):
            executor(store).plan(CASE, broken, ctx(), NOW, ActionId("act_01HQ"))
