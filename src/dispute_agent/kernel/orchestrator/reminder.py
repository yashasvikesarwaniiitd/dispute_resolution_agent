"""The reminder step: SELLER_REMINDER fired and the seller has not replied (T27, T28, X5).

Deterministic - no model. It drafts the reminder from the pack's template, asking for exactly
the items the evidence request asked for (read from that request's proposal, never from a
reply), and puts it through the same policy as every other message. Three endings:

- RequireApproval (the pilot: reminders are reviewed, D6) -> an approval request, T27.
- Allow -> planned through the executor, T28.
- Deny or Escalate (for example the contact limit) -> REMINDER_SKIPPED and a rest. The
  decision deadline is untouched, so a skipped reminder never extends the seller's window.

The decision timer is not reset by a reminder either way (T19): a case cannot wait forever by
being reminded forever.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.actions import ActionIntent, ApprovalRequest, MessagePayload
from dispute_agent.kernel.domain.enums import (
    ActionType,
    ApprovalSubject,
    IntentSource,
    IntentStatus,
    InternalEventType,
    MessageKind,
    PolicyStage,
    ProposalAction,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import KillSwitchEngaged, PolicyRefused
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, IntentId
from dispute_agent.kernel.domain.reasoning import DraftContext
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.policy.verdicts import PolicyContext

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.evidence import ChargeAssessment
    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import DraftedMessage, Proposal
    from dispute_agent.kernel.executor.executor import ActionExecutor
    from dispute_agent.kernel.ports import IdGenerator

NEXT_STEP = "If we do not hear from you, we will close this dispute without a change."


class Drafter(Protocol):
    def draft(self, ctx: DraftContext, *, shipment_ref: str) -> DraftedMessage: ...


class Policy(Protocol):
    def evaluate(self, ctx: PolicyContext) -> object: ...


class Proposals(Protocol):
    def for_case(self, case_id: CaseId) -> Sequence[Proposal]: ...


class Assessments(Protocol):
    def latest(self, case_id: CaseId) -> ChargeAssessment | None: ...


class Intents(Protocol):
    def add(self, intent: ActionIntent) -> None: ...


class Approvals(Protocol):
    def add(self, request: ApprovalRequest) -> object: ...


@dataclass
class ReminderStep:
    drafter: Drafter
    policy: Policy
    proposals: Proposals
    assessments: Assessments
    intents: Intents
    approvals: Approvals
    ids: IdGenerator
    executor: ActionExecutor | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    config: PolicyConfig = field(default_factory=PolicyConfig)
    name: str = "reminder"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        requested = self._requested_items(case)
        assessment = self.assessments.latest(case.id)
        if assessment is None or not requested:
            return _skipped("no evidence request on record to remind about")
        drafted = self.drafter.draft(
            DraftContext(
                case_id=case.id,
                case_ref=str(case.id),
                kind=MessageKind.REMINDER,
                facts=assessment.facts,
                seller_ref=case.facts.seller_ref,
                requested_items=requested,
                next_step=NEXT_STEP,
            ),
            shipment_ref=case.facts.shipment_ref,
        )
        if drafted.blocked_reason:
            return _skipped(f"draft blocked: {drafted.blocked_reason}")
        payload = MessagePayload(
            kind=MessageKind.REMINDER,
            to=case.facts.seller_contact,  # the case's own contact, never a reply's (R07)
            subject=drafted.subject,
            body=drafted.body,
            numbers_used=drafted.numbers_used,
        )
        intent = ActionIntent(
            id=IntentId(self.ids.new_id("int")),
            case_id=case.id,
            source_kind=IntentSource.TIMER,
            source_ref="SELLER_REMINDER",
            action_type=ActionType.SEND_MESSAGE,
            payload=payload,
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            assessment_id=assessment.id,
            status=IntentStatus.PENDING_APPROVAL,
            created_at=now,
        )
        ctx = PolicyContext(
            case=case,
            stage=PolicyStage.PLANNING,
            intent=intent,
            assessment=assessment,
            modules=self.modules,
            policy=self.config,
            now=now,
        )
        verdict = self.policy.evaluate(ctx)
        kind = getattr(verdict, "verdict", None)
        if kind is VerdictKind.REQUIRE_APPROVAL:
            return self._ask(case, intent, now)
        if kind is VerdictKind.ALLOW and self.executor is not None:
            return self._plan(case, intent, ctx, now)
        reasons = [
            f"{r.rule_id}: {r.reason}"
            for r in getattr(verdict, "results", ())
            if getattr(r, "verdict", None) is kind and kind is not VerdictKind.ALLOW
        ]
        return _skipped(reasons[0] if reasons else f"policy said {kind}")

    def _requested_items(self, case: Case) -> tuple[str, ...]:
        """The items the latest evidence request asked for - the reminder asks for no more."""
        for proposal in reversed(list(self.proposals.for_case(case.id))):
            if proposal.action is ProposalAction.REQUEST_SELLER_EVIDENCE:
                return proposal.action_parameters.requested_items
        return ()

    def _ask(self, case: Case, intent: ActionIntent, now: datetime) -> StepOutcome:
        self.intents.add(intent)
        approval_id = ApprovalId(self.ids.new_id("apr"))
        self.approvals.add(
            ApprovalRequest(
                approval_id=approval_id,
                case_id=case.id,
                intent_id=intent.id,
                subject=ApprovalSubject.MESSAGE,
                payload_hash=intent.payload_hash,
                message_kind=MessageKind.REMINDER,
                requested_at=now,
                expires_at=now + timedelta(hours=self.config.approvals.expire_after_hours),
            )
        )
        return StepOutcome(
            trigger=Trigger.REMINDER_REQUIRE_APPROVAL,
            detail="reminder awaits approval",
            pending_intent_id=intent.id,
            notes=(
                (InternalEventType.INTENT_CREATED, f"{intent.id}: reminder"),
                (InternalEventType.APPROVAL_REQUESTED, f"{approval_id} for {intent.id}"),
            ),
        )

    def _plan(
        self, case: Case, intent: ActionIntent, ctx: PolicyContext, now: datetime
    ) -> StepOutcome:
        assert self.executor is not None
        planned = intent.model_copy(update={"status": IntentStatus.PLANNED})
        self.intents.add(planned)
        try:
            action, _ = self.executor.plan(
                case, planned, ctx, now, ActionId(self.ids.new_id("act"))
            )
        except (PolicyRefused, KillSwitchEngaged) as refused:
            return _skipped(f"execution re-check: {refused}")
        return StepOutcome(
            trigger=Trigger.REMINDER_ALLOW,
            guards={"intent_is_not_credit": True, "action_planned": True},
            detail="reminder planned",
            pending_intent_id=planned.id,
            action_seq=action.action_seq,
            notes=((InternalEventType.INTENT_CREATED, f"{planned.id}: reminder, planned"),),
        )


def _skipped(why: str) -> StepOutcome:
    """A rest with REMINDER_SKIPPED on record (X5). The deadline still runs."""
    return StepOutcome(detail=why[:300], notes=((InternalEventType.REMINDER_SKIPPED, why[:300]),))


__all__ = ["ReminderStep"]
