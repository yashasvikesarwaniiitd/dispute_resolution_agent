"""The stations after a decision: apply the approval, execute, verify (orchestrator.md 3, I2-I4, I13).

These three carry a planned action from "a person said yes" to "it is done and proven":

- **apply_approval** (AWAITING_APPROVAL) reads the approval for the case's pending intent. Still
  pending: rest. Approved: plan the action through the executor, which re-checks every rule
  at EXECUTION and binds the approval (I3, I13) - T10. Rejected: an evidence request goes back
  to investigate (T11, within the rejection cap); a credit or a follow-up goes to a person
  (D40: a reviewer who disagrees with a code-proven amount owns the case).
- **execute** (EXECUTING) asks the executor to perform the action exactly once (T17). Anything
  but SENT goes to a person: FAILED is proven not done, UNKNOWN may have been done (I4).
- **verify** (VERIFYING) reads the far end back and compares it with the approved plan. Not
  visible yet: rest, until the deadline, then UNKNOWN and a person. Verified: move on by what
  was verified (T18-T25). A verified credit is followed by its confirmation message, which needs
  its own approval (T22); a verified confirmation resolves the case (T24) - ADR 0012: credited
  *and told*.

The write ports are supplied per action type by the composition root, so the kernel holds no
channel, ticketing system or ledger of its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.actions import ActionIntent, ApprovalRequest, MessagePayload
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ApprovalDecision,
    ApprovalSubject,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
    ReviewCode,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import (
    DomainValidationError,
    KillSwitchEngaged,
    ModuleUnavailable,
    NotFound,
    PolicyRefused,
    PortError,
)
from dispute_agent.kernel.domain.evidence import Fact
from dispute_agent.kernel.domain.external import IssueRef, SendReceipt, TransactionRef
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, IntentId
from dispute_agent.kernel.domain.models import ReviewReason
from dispute_agent.kernel.domain.reasoning import DraftContext
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.executor.executor import Outcome
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.policy.verdicts import PolicyContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, Approval, PolicyVerdict
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import DraftedMessage
    from dispute_agent.kernel.executor.executor import ActionExecutor, WritePort
    from dispute_agent.kernel.ports import (
        ApprovalStore,
        AssessmentStore,
        IdGenerator,
        IntentStore,
        Ledger,
    )

#: Verification gives up after this long and the action becomes UNKNOWN (ADR 0010).
VERIFY_DEADLINE = timedelta(seconds=600)

#: verified kind -> the state machine guard naming it (transitions.yaml).
_KIND_GUARDS = {
    "evidence_request": "verified_kind_request",
    "clarification_request": "verified_kind_request",
    "reminder": "verified_kind_reminder",
    "remeasurement": "verified_kind_ops",
    "upheld_explanation": "verified_kind_upheld",
    "credit": "verified_kind_credit",
    "credit_confirmation": "verified_kind_confirmation",
    "handed_to_human_notice": "verified_kind_notice",
}


class Actions(Protocol):
    """The parts of the action store these steps read."""

    def get(self, action_id: ActionId) -> Action: ...
    def for_intent(self, intent_id: IntentId) -> Action | None: ...
    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action: ...
    def has_verified_credit(self, case_id: object) -> bool: ...
    def credited_on_shipment(self, shipment_ref: str) -> tuple[Money, Money]: ...


class ConfirmationDrafter(Protocol):
    def draft(self, ctx: DraftContext, *, shipment_ref: str) -> DraftedMessage: ...


class Policy(Protocol):
    def evaluate(self, ctx: PolicyContext) -> PolicyVerdict: ...


_VERIFIED = ActionStatus.VERIFIED.value


def _escalate(code: ReviewCode, detail: str, module: str | None = None) -> StepOutcome:
    return StepOutcome(
        trigger=Trigger.ESCALATE,
        review_reason=ReviewReason(code=code, module=module, detail=detail[:300]),
        detail=detail[:300],
    )


def _pending(case: Case, intents: IntentStore) -> ActionIntent | StepOutcome:
    if case.pending_intent_id is None:
        return _escalate(ReviewCode.INTERNAL_ERROR, "no pending plan on the case")
    return intents.get(case.pending_intent_id)


@dataclass
class ApplyApprovalStep:
    """AWAITING_APPROVAL: turn a person's decision into a planned action, or a reason."""

    intents: IntentStore
    approvals: ApprovalStore
    actions: Actions
    executor: ActionExecutor
    ids: IdGenerator
    ledger: Ledger | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    config: PolicyConfig = field(default_factory=PolicyConfig)
    assessments: AssessmentStore | None = None
    name: str = "apply_approval"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        intent = _pending(case, self.intents)
        if isinstance(intent, StepOutcome):
            return intent
        approval = self.approvals.for_intent(intent.id)
        if approval is None:
            return _escalate(ReviewCode.INTERNAL_ERROR, "the pending plan has no approval")

        decision = approval.decision
        if decision is ApprovalDecision.PENDING:
            if now >= approval.request.expires_at:
                return _escalate(ReviewCode.APPROVAL_EXPIRED, "nobody decided before it expired")
            return StepOutcome()  # rest: waiting for a person
        decider = approval.decided_by.id if approval.decided_by is not None else ""
        if decider not in self.config.approvals.approver_ids:
            # T09: a decision by anyone not on the approver list is not a decision. The
            # store only proves a *person* decided (human:*); the list says *which* (D27).
            return _escalate(ReviewCode.POLICY_DENIED, "decided by someone who is not an approver")
        if decision is ApprovalDecision.REJECTED:
            return self._rejected(case, intent, approval)
        if decision is not ApprovalDecision.APPROVED:
            return _escalate(ReviewCode.APPROVAL_EXPIRED, f"approval is {decision.value}")
        return self._approved(case, intent, now)

    def _rejected(self, case: Case, intent: ActionIntent, approval: Approval) -> StepOutcome:
        if intent.action_type is ActionType.POST_CREDIT:
            # D40: the reviewer disagrees with a code-proven amount. A person owns the case.
            return _escalate(ReviewCode.CREDIT_REJECTED, approval.note or "credit rejected")
        if intent.source_kind is IntentSource.FOLLOW_UP:
            return _escalate(ReviewCode.FOLLOW_UP_REJECTED, approval.note or "follow-up rejected")
        if case.rejection_count + 1 > self.config.approvals.max_rejections_per_case:
            return _escalate(ReviewCode.APPROVAL_REJECTED_LIMIT, "rejected too many times")
        guards = {
            "approval_matches_pending": True,
            "pending_source_proposal": intent.source_kind is IntentSource.PROPOSAL,
            "pending_source_timer": intent.source_kind is IntentSource.TIMER,
            "intent_is_not_credit": True,
            "rejection_cap_ok": True,
        }
        return StepOutcome(trigger=Trigger.APPROVAL_REJECTED, guards=guards, detail="rejected")

    def _approved(self, case: Case, intent: ActionIntent, now: datetime) -> StepOutcome:
        ctx = PolicyContext(
            case=case,
            stage=PolicyStage.EXECUTION,
            intent=intent,
            assessment=self._assessment(intent),
            modules=self.modules,
            policy=self.config,
            now=now,
            kernel_facts=_kernel_facts(intent, self.intents, self.actions),
        )
        if intent.action_type is ActionType.POST_CREDIT:
            ctx = ctx.model_copy(update=_credit_ctx(case, self.actions, self.ledger))
        try:
            action, _ = self.executor.plan(case, intent, ctx, now, ActionId(self.ids.new_id("act")))
        except KillSwitchEngaged:
            return _escalate(ReviewCode.KILL_SWITCH_ACTIVE, "kill switch engaged at planning")
        except PolicyRefused as refused:
            return _escalate(ReviewCode.POLICY_DENIED, f"execution re-check: {refused.reason}")
        return StepOutcome(
            trigger=Trigger.APPROVAL_GRANTED,
            guards={"approval_matches_pending": True, "action_planned": True},
            detail="action planned",
            action_seq=action.action_seq,
        )

    def _assessment(self, intent: ActionIntent) -> object:
        if self.assessments is None or intent.assessment_id is None:
            return None
        return self.assessments.get(intent.assessment_id)


@dataclass
class ExecuteStep:
    """EXECUTING: perform the planned action exactly once (I2)."""

    intents: IntentStore
    actions: Actions
    executor: ActionExecutor
    ports: Callable[[Case, ActionType], WritePort]
    checkpoint: Callable[[], None] | None = None
    name: str = "execute"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        intent = _pending(case, self.intents)
        if isinstance(intent, StepOutcome):
            return intent
        action = self.actions.for_intent(intent.id)
        if action is None:
            return _escalate(ReviewCode.INTERNAL_ERROR, "no planned action for the pending plan")
        if action.status is ActionStatus.SENT:
            return StepOutcome(trigger=Trigger.ACTION_SENT, detail="already sent")
        try:
            result = self.executor.execute(
                action.id,
                self.ports(case, action.action_type),
                intent.payload,
                now,
                checkpoint=self.checkpoint,
            )
        except KillSwitchEngaged:
            return _escalate(ReviewCode.KILL_SWITCH_ACTIVE, "kill switch engaged at execution")
        except ModuleUnavailable as unavailable:
            return _escalate(ReviewCode.MODULE_UNAVAILABLE, "execution", module=unavailable.module)
        if result.outcome is Outcome.SENT:
            return StepOutcome(trigger=Trigger.ACTION_SENT, detail=result.reason)
        if result.outcome is Outcome.UNKNOWN:
            # It may have happened. Never a retry, never a success (I4).
            return _escalate(ReviewCode.ACTION_UNKNOWN, result.reason)
        if result.outcome is Outcome.BLOCKED:
            return self._blocked(action.id, result.reason)
        return _escalate(ReviewCode.ACTION_FAILED, result.reason)

    def _blocked(self, action_id: ActionId, reason: str) -> StepOutcome:
        """Nothing was sent by this call. What to do depends on why (safety review, 3).

        Another worker holding the claim is normal: rest, the sweep comes back. An action that
        was already started is described by its row: UNKNOWN may have happened (I4), FAILED
        did not, and anything else is a state this step should never see - a person decides.
        """
        if reason == "claimed_elsewhere":
            return StepOutcome()
        status = self.actions.get(action_id).status
        if status is ActionStatus.FAILED:
            return _escalate(ReviewCode.ACTION_FAILED, f"{reason}: {status.value}")
        return _escalate(ReviewCode.ACTION_UNKNOWN, f"{reason}: {status.value}")


@dataclass
class VerifyStep:
    """VERIFYING: read the far end back, then move on by what was verified (I2, ADR 0012)."""

    intents: IntentStore
    actions: Actions
    executor: ActionExecutor
    ports: Callable[[Case, ActionType], WritePort]
    policy: Policy
    approvals: ApprovalStore
    drafter: ConfirmationDrafter
    ids: IdGenerator
    assessments: AssessmentStore | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    config: PolicyConfig = field(default_factory=PolicyConfig)
    name: str = "verify"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        intent = _pending(case, self.intents)
        if isinstance(intent, StepOutcome):
            return intent
        action = self.actions.for_intent(intent.id)
        if action is None or action.external_ref is None:
            return _escalate(ReviewCode.INTERNAL_ERROR, "nothing sent to verify")
        port = self.ports(case, action.action_type)
        result = self.executor.verify(action.id, port, _receipt(action), intent.payload, now)
        if result.outcome is Outcome.BLOCKED and result.reason == f"not_sent:{_VERIFIED}":
            # Verified on an earlier pass that crashed before moving the case on. The proof
            # is on the row; reading it again would only fail on the status (review, 6).
            result = replace(result, outcome=Outcome.VERIFIED)
        elif result.outcome is Outcome.BLOCKED and result.reason != "not_visible_yet":
            # Not SENT and not verified: there is nothing out there this step can prove.
            return _escalate(ReviewCode.VERIFICATION_FAILED, result.reason)
        if result.outcome is Outcome.BLOCKED:
            if action.sent_at is not None and now - action.sent_at > VERIFY_DEADLINE:
                self.actions.set_status(
                    action.id,
                    ActionStatus.UNKNOWN,
                    now,
                    last_error_code="verify_deadline_passed",
                )
                return _escalate(ReviewCode.VERIFICATION_FAILED, "not visible by the deadline")
            return StepOutcome()  # rest: not visible yet
        if result.outcome is not Outcome.VERIFIED:
            return _escalate(ReviewCode.VERIFICATION_FAILED, result.reason)

        kind = _verified_kind(intent)
        guards = dict.fromkeys(set(_KIND_GUARDS.values()), False)
        guards[_KIND_GUARDS[kind]] = True
        # Supplied for every kind: T24 names it, and the machine never assumes a missing
        # guard holds - a notice or reminder must not stop the case with a ConfigError.
        guards["credit_verified_for_case"] = False
        if kind == "credit":
            return self._confirm_credit(case, intent, now)
        if kind == "credit_confirmation":
            guards["credit_verified_for_case"] = self.actions.has_verified_credit(case.id)
        return StepOutcome(trigger=Trigger.ACTION_VERIFIED, guards=guards, detail="verified")

    def _confirm_credit(self, case: Case, credit: ActionIntent, now: datetime) -> StepOutcome:
        """T22: the credit is verified; tell the seller, through the normal approval path."""
        amount = getattr(credit.payload, "amount", None)
        if not isinstance(amount, Money):
            return _escalate(ReviewCode.INTERNAL_ERROR, "a verified credit with no amount")
        # Equal to the ledger transaction to the paisa: verification just compared them.
        credited = Fact(fact_id="credited_amount", label="amount credited", value=amount)
        assessment = (
            self.assessments.get(credit.assessment_id)
            if self.assessments is not None and credit.assessment_id is not None
            else None
        )
        facts = (*(assessment.facts if assessment else ()), credited)
        drafted = self.drafter.draft(
            DraftContext(
                case_id=case.id,
                case_ref=str(case.id),
                kind=MessageKind.CREDIT_CONFIRMATION,
                facts=facts,
                seller_ref=case.facts.seller_ref,
                next_step="No further action is needed.",
            ),
            shipment_ref=case.facts.shipment_ref,
        )
        if drafted.blocked_reason:
            # Credited but not told: a person must tell the seller (S25).
            return _escalate(
                ReviewCode.POLICY_ESCALATED, f"draft_blocked: {drafted.blocked_reason}"
            )
        payload = MessagePayload(
            kind=MessageKind.CREDIT_CONFIRMATION,
            to=case.facts.seller_contact,
            subject=drafted.subject,
            body=drafted.body,
            numbers_used=drafted.numbers_used,
        )
        intent = ActionIntent(
            id=IntentId(self.ids.new_id("int")),
            case_id=case.id,
            source_kind=IntentSource.FOLLOW_UP,
            source_ref=str(credit.id),
            action_type=ActionType.SEND_MESSAGE,
            payload=payload,
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            assessment_id=credit.assessment_id,
            status=IntentStatus.PENDING_APPROVAL,
            created_at=now,
        )
        verdict = self.policy.evaluate(
            PolicyContext(
                case=case,
                stage=PolicyStage.PLANNING,
                intent=intent,
                assessment=assessment,
                modules=self.modules,
                policy=self.config,
                now=now,
                kernel_facts=(credited,),
            )
        )
        if verdict.verdict is not VerdictKind.REQUIRE_APPROVAL:
            reason = next(
                (
                    f"{r.rule_id}: {r.reason}"
                    for r in verdict.results
                    if r.verdict is verdict.verdict
                ),
                verdict.verdict.name,
            )
            return _escalate(ReviewCode.FOLLOW_UP_DENIED, reason)
        self.intents.add(intent)
        self.approvals.add(
            ApprovalRequest(
                approval_id=ApprovalId(self.ids.new_id("apr")),
                case_id=case.id,
                intent_id=intent.id,
                subject=ApprovalSubject.MESSAGE,
                payload_hash=intent.payload_hash,
                message_kind=MessageKind.CREDIT_CONFIRMATION,
                requested_at=now,
                expires_at=now + timedelta(hours=self.config.approvals.expire_after_hours),
            )
        )
        return StepOutcome(
            trigger=Trigger.FOLLOW_UP_REQUIRE_APPROVAL,
            guards={"verified_kind_credit": True},
            detail="credit verified; confirmation awaits approval",
            pending_intent_id=intent.id,
        )


def _verified_kind(intent: ActionIntent) -> str:
    if intent.action_type is ActionType.POST_CREDIT:
        return "credit"
    if intent.action_type is ActionType.UPSERT_ISSUE:
        return "remeasurement"
    kind = getattr(intent.payload, "kind", None)
    return kind.value if isinstance(kind, MessageKind) else "evidence_request"


def _receipt(action: Action) -> object:
    """Rebuild the receipt the port reads back from, from what the action recorded."""
    ref = action.external_ref or ""
    if action.action_type is ActionType.POST_CREDIT:
        return TransactionRef(transaction_id=ref)
    if action.action_type is ActionType.UPSERT_ISSUE:
        return IssueRef(key=ref, id=ref)
    return SendReceipt(external_message_id=ref, sent_at=action.sent_at or action.planned_at)


def _kernel_facts(intent: ActionIntent, intents: IntentStore, actions: Actions) -> tuple[Fact, ...]:
    """The credited amount a confirmation may cite, re-derived from the credit it confirms.

    It used to be read from the confirmation's own `numbers_used` - the message vouching for
    itself, so an edited number would have validated against itself (safety review, 5). Now
    it is the amount of the credit intent the confirmation follows, and only once that
    credit's action is VERIFIED. Anything missing or unreadable gives no fact, so R09 denies
    the number rather than trusting it.
    """
    if getattr(intent.payload, "kind", None) is not MessageKind.CREDIT_CONFIRMATION:
        return ()
    if intent.source_kind is not IntentSource.FOLLOW_UP or not intent.source_ref:
        return ()
    try:
        credit = intents.get(IntentId(intent.source_ref))
        action = actions.for_intent(credit.id)
    except (NotFound, PortError, DomainValidationError):
        return ()
    amount = getattr(credit.payload, "amount", None)
    if (
        credit.case_id != intent.case_id
        or credit.action_type is not ActionType.POST_CREDIT
        or action is None
        or action.status is not ActionStatus.VERIFIED
        or not isinstance(amount, Money)
    ):
        return ()
    return (Fact(fact_id="credited_amount", label="amount credited", value=amount),)


def _credit_ctx(case: Case, actions: Actions, ledger: Ledger | None) -> dict[str, object]:
    """Fresh counts for R15/R16 at EXECUTION. Unknown stays unknown (refused, not zero)."""
    credited = actions.credited_on_shipment(case.facts.shipment_ref)
    debited: Money | None = None
    if ledger is not None:
        try:
            debited = Money.from_paise(
                sum(
                    e.amount.to_paise()
                    for e in ledger.get_entries(case.facts.shipment_ref)
                    if e.type == "DEBIT"
                )
            )
        except (PortError, ModuleUnavailable, DomainValidationError):
            debited = None
    return {
        "credited_on_shipment": credited[0],
        "credited_duplicate_on_shipment": credited[1],
        "debited_on_shipment": debited,
    }


__all__ = ["VERIFY_DEADLINE", "ApplyApprovalStep", "ExecuteStep", "VerifyStep"]
