"""The hand-off notice: a person took the case and asked for the seller to be told (T34-T36).

Run by dispatch for a HUMAN_TOOK_CASE with `notify_seller=true` (T33, without a notice, is
applied by dispatch directly). Deterministic, like the reminder: the pack's template, the same
policy as every message, and in the pilot your approval (D6).

- RequireApproval -> T34, the notice waits in the review queue; verified, the case is HANDED_TO_HUMAN (T25).
- Allow -> T35, planned through the executor.
- Anything else (a blocked draft, a denial) -> T36: the person owns the case either way, and
  NOTICE_NOT_SENT says plainly that the seller was not told.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.actions import ActionIntent, ApprovalRequest, MessagePayload
from dispute_agent.kernel.domain.enums import (
    ActionType,
    ApprovalSubject,
    IntentSource,
    IntentStatus,
    InternalEventType,
    MessageKind,
    PolicyStage,
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
    from collections.abc import Mapping
    from datetime import datetime

    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.executor.executor import ActionExecutor
    from dispute_agent.kernel.orchestrator.reminder import (
        Approvals,
        Assessments,
        Drafter,
        Intents,
        Policy,
    )
    from dispute_agent.kernel.ports import IdGenerator

#: The guards T34-T36 read. Dispatch has already checked the actor is a person (P3).
HUMAN_ASKED = {"actor_is_human": True, "notify_seller": True}


@dataclass
class HandOffStep:
    drafter: Drafter
    policy: Policy
    assessments: Assessments
    intents: Intents
    approvals: Approvals
    ids: IdGenerator
    executor: ActionExecutor | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    config: PolicyConfig = field(default_factory=PolicyConfig)
    name: str = "hand_off"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        assessment = self.assessments.latest(case.id)
        drafted = self.drafter.draft(
            DraftContext(
                case_id=case.id,
                case_ref=str(case.id),
                kind=MessageKind.HANDED_TO_HUMAN_NOTICE,
                facts=assessment.facts if assessment else (),
                seller_ref=case.facts.seller_ref,
                next_step="A member of our team will contact you.",
            ),
            shipment_ref=case.facts.shipment_ref,
        )
        if drafted.blocked_reason:
            return _not_sent(f"draft blocked: {drafted.blocked_reason}")
        payload = MessagePayload(
            kind=MessageKind.HANDED_TO_HUMAN_NOTICE,
            to=case.facts.seller_contact,
            subject=drafted.subject,
            body=drafted.body,
            numbers_used=drafted.numbers_used,
        )
        intent = ActionIntent(
            id=IntentId(self.ids.new_id("int")),
            case_id=case.id,
            source_kind=IntentSource.FOLLOW_UP,
            source_ref="hand_off",
            action_type=ActionType.SEND_MESSAGE,
            payload=payload,
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            assessment_id=assessment.id if assessment else None,
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
        return _not_sent(reasons[0] if reasons else f"policy said {kind}")

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
                message_kind=MessageKind.HANDED_TO_HUMAN_NOTICE,
                requested_at=now,
                expires_at=now + timedelta(hours=self.config.approvals.expire_after_hours),
            )
        )
        return StepOutcome(
            trigger=Trigger.FOLLOW_UP_REQUIRE_APPROVAL,
            guards=dict(HUMAN_ASKED),
            detail="hand-off notice awaits approval",
            pending_intent_id=intent.id,
            notes=(
                (InternalEventType.INTENT_CREATED, f"{intent.id}: handed_to_human_notice"),
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
            return _not_sent(f"execution re-check: {refused}")
        return StepOutcome(
            trigger=Trigger.FOLLOW_UP_ALLOW,
            guards={**HUMAN_ASKED, "intent_is_not_credit": True, "action_planned": True},
            detail="hand-off notice planned",
            pending_intent_id=planned.id,
            action_seq=action.action_seq,
        )


def _not_sent(why: str) -> StepOutcome:
    """T36: the person owns the case; the trail says the seller was not told."""
    return StepOutcome(
        trigger=Trigger.FOLLOW_UP_BLOCKED,
        guards=dict(HUMAN_ASKED),
        detail=why[:300],
        notes=((InternalEventType.NOTICE_NOT_SENT, why[:300]),),
    )


__all__ = ["HandOffStep"]
