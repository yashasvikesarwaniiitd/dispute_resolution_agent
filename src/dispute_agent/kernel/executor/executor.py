"""The action executor (executor.md 1-6, ADR 0006, 0007, 0010; I2, I3, I4, I10).

The only part of the system allowed to change anything outside it, and the shape of it is a
careful clerk: write down what you are about to do, check again that you are allowed, look
whether you already did it, act, then go and read the result back from the other system.

Four things here are load-bearing, and each is a decision rather than an implementation
detail.

**`plan` takes no "already approved" parameter.** There is no argument by which a caller can
assert that something was authorised. The executor finds the approval itself, from the store,
and checks it covers this exact intent. That is what makes the executor safe to call from
anywhere: a bug upstream cannot talk it into posting a credit (I3).

**The kill switch is checked inside `plan` and inside `execute`**, not at the entry point.
A check only at the top would let an action planned a second before the switch went on still
reach the outside world (I10).

**An ambiguous outcome becomes UNKNOWN, never a retry and never a success.** If a write may
have happened, trying again could do it twice, and calling it a failure could lose a credit
that was actually posted. UNKNOWN sends the case to a person, who is the only one allowed to
resolve it (I4).

**Verification reads the far end.** A receipt is what the adapter says happened; a snapshot is
what is actually there. Only the second settles it (I2, ADR 0010).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    PolicyStage,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    KillSwitchEngaged,
    PermanentPortError,
    PolicyRefused,
    TransientPortError,
)
from dispute_agent.kernel.executor.keys import idempotency_key

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, ActionIntent, Approval, PolicyVerdict
    from dispute_agent.kernel.domain.ids import ActionId, IdempotencyKey
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.policy.engine import PolicyEngine
    from dispute_agent.kernel.policy.verdicts import PolicyContext


class Outcome(StrEnum):
    """What one call of `execute` or `verify` concluded."""

    SENT = "sent"
    VERIFIED = "verified"
    FAILED = "failed"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ExecutionResult:
    """The answer, with the reason attached so the caller never has to infer it."""

    outcome: Outcome
    action_id: ActionId
    reason: str = ""
    external_ref: str | None = None

    @property
    def is_success(self) -> bool:
        """UNKNOWN is deliberately not success (I4). Nor is it failure."""
        return self.outcome in (Outcome.SENT, Outcome.VERIFIED)


class WritePort(Protocol):
    """What the executor needs from any write adapter.

    Narrow on purpose: send with a key, look up by that key, read back. The three write
    modules differ enormously underneath and not at all here.
    """

    idempotent_writes: bool
    lookup_supported: bool

    def send(self, payload: object, key: IdempotencyKey) -> object: ...
    def find_sent(self, key: IdempotencyKey) -> object | None: ...
    def read_back(self, receipt: object) -> object: ...


class ActionStore(Protocol):
    """The actions table, as the executor uses it."""

    def insert(self, action: Action) -> Action: ...
    def get(self, action_id: ActionId) -> Action: ...
    def claim(self, action_id: ActionId, worker: str, now: datetime, lease_s: int) -> bool: ...
    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action: ...
    def find_approval_for(self, intent_id: object) -> Approval | None: ...
    def live_credits(self, shipment_ref: str, reason_code: str) -> int: ...


class KillSwitch(Protocol):
    def is_engaged(self) -> bool: ...


class ActionExecutor:
    """plan, then execute, then verify. Each one commits before the next begins."""

    def __init__(
        self,
        policy: PolicyEngine,
        actions: ActionStore,
        kill_switch: KillSwitch,
        *,
        worker: str = "runner-1",
        claim_lease_s: int = 420,
        execute_budget_s: int = 300,
    ) -> None:
        if claim_lease_s <= execute_budget_s:
            # executor.md section 3. A lease shorter than the work it protects would expire
            # mid-write, and a second worker would start the same action.
            raise ValueError(
                f"claim_lease_s ({claim_lease_s}) must exceed execute_budget_s ({execute_budget_s})"
            )
        self._policy = policy
        self._actions = actions
        self._kill_switch = kill_switch
        self._worker = worker
        self._lease_s = claim_lease_s
        self._budget_s = execute_budget_s

    # -- plan ------------------------------------------------------------------------------

    def plan(
        self,
        case: Case,
        intent: ActionIntent,
        ctx: PolicyContext,
        now: datetime,
        action_id: ActionId,
    ) -> tuple[Action, PolicyVerdict]:
        """Re-check policy at EXECUTION, bind the approval, write the PLANNED row.

        The PLANNED row and its key are committed **before** any external call, so a crash
        between planning and executing leaves something recoverable rather than a write
        nobody recorded (ADR 0006).
        """
        if self._kill_switch.is_engaged():
            raise KillSwitchEngaged("planning")

        # The approval is found here and put into the context, so the rules judge the real
        # one. Evaluating first and binding afterwards would ask R10 and R13 to rule on an
        # approval they cannot see, and they would correctly deny every message.
        approval = self._actions.find_approval_for(intent.id)

        verdict = self._policy.evaluate(
            ctx.model_copy(
                update={
                    "stage": PolicyStage.EXECUTION,
                    "intent": intent,
                    "approval": approval,
                    "now": now,
                }
            )
        )
        if verdict.verdict is VerdictKind.DENY or verdict.verdict is VerdictKind.ESCALATE:
            raise PolicyRefused(verdict.verdict.name, _first_reason(verdict))

        # executor.md section 4 step 2: ALLOW, or REQUIRE_APPROVAL with an approval that
        # actually covers this intent. The binding is re-checked here rather than trusted
        # from the verdict, because this is the last point before a row exists.
        if verdict.verdict is VerdictKind.REQUIRE_APPROVAL:
            approval = self._require_binding(intent, approval, now)
        elif intent.action_type is ActionType.POST_CREDIT:
            # A credit must never reach ALLOW without an approval. If it somehow did, the
            # rule set is wrong and the safe answer is to stop (I3).
            approval = self._require_binding(intent, approval, now)

        action_seq = case.action_seq + 1
        key = idempotency_key(case.id, intent.action_type, action_seq, intent.payload_hash)

        from dispute_agent.kernel.domain.actions import Action as _Action

        action = _Action(
            id=action_id,
            case_id=case.id,
            intent_id=intent.id,
            approval_id=None if approval is None else approval.id,
            action_type=intent.action_type,
            action_seq=action_seq,
            idempotency_key=key,
            payload_hash=intent.payload_hash,
            status=ActionStatus.PLANNED,
            planned_at=now,
        )
        return self._actions.insert(action), verdict

    def _require_binding(
        self, intent: ActionIntent, approval: Approval | None, now: datetime
    ) -> Approval:
        """The approval must cover this exact intent, or nothing is planned.

        Every condition here is also in `tr_actions_approval_binding`, so an action row could
        not be created even if this method were deleted (I3). Two layers saying the same
        thing is the point: one of them is code someone could change in a hurry.
        """
        if approval is None:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_missing_or_unbound")
        if approval.decision is not ApprovalDecision.APPROVED:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_missing_or_unbound")
        if approval.used_by_action_id is not None:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_missing_or_unbound")
        if approval.request.intent_id != intent.id:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_missing_or_unbound")
        if approval.request.payload_hash != intent.payload_hash:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_missing_or_unbound")
        if approval.request.expires_at <= now:
            raise PolicyRefused("REQUIRE_APPROVAL", "approval_stale")
        if intent.action_type is ActionType.POST_CREDIT:
            amount = getattr(intent.payload, "amount", None)
            if approval.request.amount != amount:
                raise PolicyRefused("DENY", "credit_amount_mismatch")
        return approval

    # -- execute ---------------------------------------------------------------------------

    def execute(
        self, action_id: ActionId, port: WritePort, payload: object, now: datetime
    ) -> ExecutionResult:
        """Claim, look before writing, write, record. Never retried blindly."""
        action = self._actions.get(action_id)
        if action.status is not ActionStatus.PLANNED:
            return ExecutionResult(Outcome.BLOCKED, action_id, "already_started")

        # Inside execute, not only at the entry point (I10). An action planned a second
        # before the switch went on must not reach the outside world.
        if self._kill_switch.is_engaged():
            self._actions.set_status(
                action_id, ActionStatus.FAILED, now, last_error_code="kill_switch_active"
            )
            return ExecutionResult(Outcome.FAILED, action_id, "kill_switch_active")

        if not self._actions.claim(action_id, self._worker, now, self._lease_s):
            # Someone else holds it. Rest: no commit, no escalation, the sweep comes back.
            return ExecutionResult(Outcome.BLOCKED, action_id, "claimed_elsewhere")

        key = action.idempotency_key

        # Look before writing. A crash after a write but before recording it is exactly the
        # case this covers, and it is the whole reason write ports support lookup.
        if port.lookup_supported:
            try:
                found = port.find_sent(key)
            except AmbiguousOutcome as exc:
                return self._to_unknown(action_id, now, f"lookup_ambiguous:{exc}")
            except TransientPortError:
                found = None
            if found is not None:
                return self._to_sent(action_id, now, found, "found_by_lookup")

        try:
            receipt = port.send(payload, key)
        except AmbiguousOutcome as exc:
            # May or may not have happened. Retrying could do it twice; calling it failed
            # could lose a credit that was posted. Neither is acceptable (I4).
            return self._to_unknown(action_id, now, f"ambiguous_write:{exc}")
        except PermanentPortError as exc:
            self._actions.set_status(
                action_id,
                ActionStatus.FAILED,
                now,
                last_error_code="permanent",
                last_error_detail=str(exc)[:300],
            )
            return ExecutionResult(Outcome.FAILED, action_id, "permanent")
        except TransientPortError as exc:
            # Nothing was written: TransientPortError means the call certainly did not take
            # effect, which is precisely why it is a different type from AmbiguousOutcome.
            self._actions.set_status(
                action_id,
                ActionStatus.FAILED,
                now,
                last_error_code="transient_exhausted",
                last_error_detail=str(exc)[:300],
            )
            return ExecutionResult(Outcome.FAILED, action_id, "transient_exhausted")

        return self._to_sent(action_id, now, receipt, "sent")

    # -- verify ----------------------------------------------------------------------------

    def verify(
        self,
        action_id: ActionId,
        port: WritePort,
        receipt: object,
        checks: dict[str, object],
        now: datetime,
    ) -> ExecutionResult:
        """Read the far end and compare. A receipt is a claim; a snapshot is evidence."""
        action = self._actions.get(action_id)
        if action.status is not ActionStatus.SENT:
            return ExecutionResult(Outcome.BLOCKED, action_id, f"not_sent:{action.status.value}")

        try:
            snapshot = port.read_back(receipt)
        except TransientPortError:
            return ExecutionResult(Outcome.BLOCKED, action_id, "not_visible_yet")

        mismatches = [
            name
            for name, expected in checks.items()
            if getattr(snapshot, name, _MISSING) != expected
        ]
        if mismatches:
            # What is out there is not what was approved. Never a success (I2, I13).
            return self._to_unknown(action_id, now, f"verification_mismatch:{mismatches[0]}")

        self._actions.set_status(
            action_id, ActionStatus.VERIFIED, now, verified_at=now, verified_by="system"
        )
        return ExecutionResult(Outcome.VERIFIED, action_id, "verified")

    # -- helpers ---------------------------------------------------------------------------

    def _to_sent(
        self, action_id: ActionId, now: datetime, receipt: object, reason: str
    ) -> ExecutionResult:
        external_ref = str(
            getattr(receipt, "external_message_id", None)
            or getattr(receipt, "transaction_id", None)
            or getattr(receipt, "key", None)
            or "unknown"
        )
        self._actions.set_status(
            action_id, ActionStatus.SENT, now, sent_at=now, external_ref=external_ref
        )
        return ExecutionResult(Outcome.SENT, action_id, reason, external_ref)

    def _to_unknown(self, action_id: ActionId, now: datetime, reason: str) -> ExecutionResult:
        self._actions.set_status(
            action_id, ActionStatus.UNKNOWN, now, unknown_at=now, last_error_code=reason[:60]
        )
        return ExecutionResult(Outcome.UNKNOWN, action_id, reason)


class _Missing:
    def __eq__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return 0


_MISSING = _Missing()


def _first_reason(verdict: PolicyVerdict) -> str:
    strictest = [r for r in verdict.results if r.verdict is verdict.verdict]
    return strictest[0].reason if strictest else verdict.verdict.name
