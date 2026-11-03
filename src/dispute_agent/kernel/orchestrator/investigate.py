"""The investigate step: decide what to do, and turn the decision into a plan (orchestrator.md 3).

This is the station that was empty. A case arrives with its evidence gathered and its charge
recalculated; this step builds a briefing, asks the reasoner what to do, and turns the answer
into something the rest of the system can act on — or into an honest hand-off to a person.

**What it may decide, and what it may not.** The reasoner proposes; code decides (I1). The
proposal is mapped to an intent by fixed rules (orchestrator.md 3.6), the intent's text is
drafted from a template, never from the proposal, and the result is judged by the policy
engine. Nothing here sends anything. The furthest this step goes is writing the plan down
and filing an approval request.

**What is deliberately not wired yet, and escalates instead of guessing:**

- `propose_credit` — credits are Phase 5, and the amount derivation that nets against
  earlier credits has no caller yet (BACKLOG.md). Taking `assessment.discrepancy` as the
  amount would pay the wrong figure on a second dispute, so this hands the case to a person.
- an ALLOW verdict, which would need to plan an action now — the action store the executor
  writes to does not exist yet. Every message kind needs review by default, so for messages
  this path does not arise; for a remeasurement issue it does, and it escalates naming why.

Escalating with a precise reason is the honest version of "not built yet". Pretending to
decide would be the dishonest one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.actions import (
    ActionIntent,
    ApprovalRequest,
    IssuePayload,
    MessagePayload,
)
from dispute_agent.kernel.domain.enums import (
    ActionType,
    ApprovalSubject,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
    ProposalAction,
    ReviewCode,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    BudgetExceeded,
    DomainValidationError,
    GuardrailBlocked,
    InvalidModelOutput,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ApprovalId, IntentId
from dispute_agent.kernel.domain.models import ReviewReason
from dispute_agent.kernel.domain.reasoning import CaseBriefing, ContactStatus, DraftContext
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.policy.verdicts import PolicyContext

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import ActionPayload, PolicyVerdict
    from dispute_agent.kernel.domain.evidence import ChargeAssessment
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import DraftedMessage, Proposal
    from dispute_agent.kernel.ports import (
        ApprovalStore,
        AssessmentStore,
        EvidenceStore,
        IdGenerator,
        IntentStore,
        Reasoner,
    )

#: Prose the template interpolates verbatim. No digits, on purpose: anything numeric here
#: would be a number with no fact behind it, and R09 would refuse the message.
NEXT_STEP = "We will decide as soon as your reply arrives."


class MenuPack(Protocol):
    """The one thing this step asks the pack for: which actions are allowed here."""

    def allowed_actions(
        self, state: object, assessment: ChargeAssessment
    ) -> Sequence[ProposalAction]: ...


class Drafter(Protocol):
    """Writes the message text from facts. The pack's template renderer implements it."""

    def draft(self, ctx: DraftContext, *, shipment_ref: str) -> DraftedMessage: ...


class Policy(Protocol):
    def evaluate(self, ctx: PolicyContext) -> PolicyVerdict: ...


@dataclass
class InvestigateStep:
    """Decide, draft, write the plan down, and ask for approval. Never sends."""

    pack: MenuPack
    reasoner: Reasoner
    drafter: Drafter
    policy: Policy
    evidence: EvidenceStore
    assessments: AssessmentStore
    intents: IntentStore
    approvals: ApprovalStore
    ids: IdGenerator
    modules: Mapping[str, bool] = field(default_factory=dict)
    config: PolicyConfig = field(default_factory=PolicyConfig)
    #: The rules-only baseline, built with `fallback_used=True`, for when `reasoner` is a
    #: model that cannot be reached (orchestrator.md section 5). None when `reasoner` is
    #: already the baseline: there is nothing further to fall back to.
    fallback: Reasoner | None = None
    name: str = "investigate"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        assessment = self.assessments.latest(case.id)
        if assessment is None:
            # assess always writes one before this step can be reached. If it is missing,
            # something upstream is broken, and deciding without the numbers is the one
            # thing this step must never do.
            return _escalate(ReviewCode.INTERNAL_ERROR, "no assessment for this round")

        briefing = self._briefing(case, assessment)
        decided = self._propose(briefing)
        if isinstance(decided, StepOutcome):
            return decided
        proposal = decided

        # The menu is code's decision, not the reasoner's (I1). Before this check the menu
        # was built, put on the briefing and then ignored, so an off-menu proposal - the
        # kernel rule that removes uphold_charge and propose_credit once money has moved
        # rests on exactly this - would have been acted on (second review, finding 3).
        if proposal.action not in briefing.allowed_actions:
            return _escalate(
                ReviewCode.INVALID_MODEL_OUTPUT,
                f"proposed {proposal.action.value}, which is not on this case's menu",
            )
        return self._act_on(case, proposal, assessment, now, briefing.allowed_actions)

    # -- asking the reasoner -------------------------------------------------------------------

    def _propose(self, briefing: CaseBriefing) -> Proposal | StepOutcome:
        """orchestrator.md section 5: who decides when the model cannot.

        Two different failures, deliberately treated differently. A model that could not be
        *reached* is replaced by the rules baseline, which decides the clear cases. A model
        that *answered badly* - invalid output, over budget, blocked, drifted - is not
        replaced: the case goes to a person with that reason (FLOW section 7b). Falling back
        there would quietly turn "the AI produced something we cannot trust" into "rules
        decided", and nobody would look at why.
        """
        try:
            return self.reasoner.propose(briefing)
        except (ModuleUnavailable, TransientPortError) as down:
            module = down.module if isinstance(down, ModuleUnavailable) else "model_gateway"
            if self.fallback is None:
                raise ModuleUnavailable(module) from down
            proposal = self.fallback.propose(briefing)
            if proposal.action is ProposalAction.ESCALATE_TO_HUMAN:
                # The baseline could not decide this one either, so the honest reason is the
                # missing model, not "the proposal said escalate" (spec section 5).
                return StepOutcome(
                    trigger=Trigger.ESCALATE,
                    review_reason=ReviewReason(
                        code=ReviewCode.MODULE_UNAVAILABLE,
                        module=module,
                        detail="the model was unavailable and the rules could not decide",
                    ),
                    detail=f"reasoner_fallback: {module}",
                )
            return proposal
        except InvalidModelOutput as invalid:
            return _escalate(ReviewCode.INVALID_MODEL_OUTPUT, str(invalid))
        except BudgetExceeded as budget:
            return _escalate(ReviewCode.LLM_BUDGET_EXCEEDED, str(budget))
        except GuardrailBlocked as blocked:
            return _escalate(ReviewCode.GUARDRAIL_BLOCKED, str(blocked))
        except AgentDrift as drift:
            return _escalate(ReviewCode.AGENT_DRIFT, str(drift))

    # -- building the briefing ---------------------------------------------------------------

    def _briefing(self, case: Case, assessment: ChargeAssessment) -> CaseBriefing:
        bundle = self.evidence.bundle(case.id, case.gather_round)
        allowed = tuple(self.pack.allowed_actions(case.state, assessment))
        return CaseBriefing(
            case_id=case.id,
            state=case.state,
            facts=case.facts,
            evidence=bundle,
            assessment=assessment,
            allowed_actions=allowed,
            # No message or ops store is wired yet, so nothing can have arrived in this
            # round. These are accurate for a first round, which is the only round this
            # step can currently be reached in.
            contact=ContactStatus(
                reply_in_round=False, clarifications_sent=0, remeasurement_outstanding=False
            ),
            briefing_hash=payload_hash(
                {
                    "case": str(case.id),
                    "round": case.gather_round,
                    "input": str(assessment.input_hash),
                }
            ),
        )

    # -- acting on the proposal ----------------------------------------------------------------

    def _act_on(
        self,
        case: Case,
        proposal: Proposal,
        assessment: ChargeAssessment,
        now: datetime,
        menu: tuple[ProposalAction, ...],
    ) -> StepOutcome:
        action = proposal.action

        if action is ProposalAction.ESCALATE_TO_HUMAN:
            return _escalate(ReviewCode.PROPOSAL_ESCALATED, proposal.rationale[:200])

        if action is ProposalAction.WAIT:
            # T09's guard. Waiting is only honest when something we asked for is coming.
            return _escalate(
                ReviewCode.WAIT_WITHOUT_EXPECTATION,
                "the proposal was to wait, and nothing is outstanding to wait for",
            )

        if action is ProposalAction.PROPOSE_CREDIT:
            return _escalate(
                ReviewCode.POLICY_ESCALATED,
                f"a credit was proposed for {proposal.action_parameters.reason_code}; "
                "credits are Phase 5 and the netted amount has no caller yet",
            )

        try:
            payload = self._payload(case, proposal, assessment)
        except DomainValidationError as blocked:
            # A template that cannot be rendered - a missing fact, or a message kind with no
            # template yet - is a draft that must not go out (orchestrator.md 3.6).
            return _escalate(ReviewCode.POLICY_ESCALATED, f"draft_blocked: {blocked}")

        action_type = (
            ActionType.SEND_MESSAGE
            if isinstance(payload, MessagePayload)
            else ActionType.UPSERT_ISSUE
        )
        intent = ActionIntent(
            id=IntentId(self.ids.new_id("int")),
            case_id=case.id,
            source_kind=IntentSource.PROPOSAL,
            source_ref=str(proposal.id),
            action_type=action_type,
            payload=payload,
            payload_hash=payload_hash(payload.model_dump(mode="json")),
            assessment_id=assessment.id,
            status=IntentStatus.PENDING_APPROVAL,
            created_at=now,
        )

        verdict = self.policy.evaluate(
            PolicyContext(
                case=case,
                stage=PolicyStage.PLANNING,
                intent=intent,
                proposal=proposal,
                assessment=assessment,
                modules=self.modules,
                policy=self.config,
                now=now,
                # So R01 checks it too - belt and braces with the check in `run`.
                allowed_actions=menu,
            )
        )
        return self._by_verdict(case, intent, verdict, now)

    def _payload(
        self, case: Case, proposal: Proposal, assessment: ChargeAssessment
    ) -> ActionPayload:
        """orchestrator.md 3.6, the rows this step can carry today."""
        action = proposal.action
        if action is ProposalAction.REQUEST_REMEASUREMENT:
            return IssuePayload(
                summary=f"Remeasure {case.facts.shipment_ref}",
                description=(
                    f"Please remeasure shipment {case.facts.shipment_ref} "
                    f"(claim {case.facts.claim_ref}) and record the result in the finding fields."
                ),
            )

        kind = (
            MessageKind.UPHELD_EXPLANATION
            if action is ProposalAction.UPHOLD_CHARGE
            else MessageKind.EVIDENCE_REQUEST
        )
        drafted = self.drafter.draft(
            DraftContext(
                case_id=case.id,
                case_ref=str(case.id),
                kind=kind,
                facts=assessment.facts,
                seller_ref=case.facts.seller_ref,
                requested_items=proposal.action_parameters.requested_items,
                next_step=NEXT_STEP,
            ),
            shipment_ref=case.facts.shipment_ref,
        )
        if drafted.blocked_reason:
            raise DomainValidationError(drafted.blocked_reason)
        return MessagePayload(
            kind=kind,
            # Always the case's own contact, never anything from a draft or a reply (R07).
            to=case.facts.seller_contact,
            subject=drafted.subject,
            body=drafted.body,
            numbers_used=drafted.numbers_used,
        )

    def _by_verdict(
        self, case: Case, intent: ActionIntent, verdict: PolicyVerdict, now: datetime
    ) -> StepOutcome:
        kind = verdict.verdict
        if kind is VerdictKind.DENY:
            return _escalate(ReviewCode.POLICY_DENIED, _first_reason(verdict))
        if kind is VerdictKind.ESCALATE:
            return _escalate(ReviewCode.POLICY_ESCALATED, _first_reason(verdict))
        if kind is VerdictKind.ALLOW:
            return _escalate(
                ReviewCode.POLICY_ESCALATED,
                f"{intent.action_type.value} was allowed without review, and planning it "
                "needs the action store, which is not wired yet",
            )

        # REQUIRE_APPROVAL: write the plan, then the request that points at it. The approval
        # binds to this hash, so the text cannot change between here and your yes (I13).
        self.intents.add(intent)
        message_kind = getattr(intent.payload, "kind", None)
        self.approvals.add(
            ApprovalRequest(
                approval_id=ApprovalId(self.ids.new_id("apr")),
                case_id=case.id,
                intent_id=intent.id,
                subject=ApprovalSubject.MESSAGE,
                payload_hash=intent.payload_hash,
                message_kind=message_kind,
                requested_at=now,
                expires_at=now + timedelta(hours=self.config.approvals.expire_after_hours),
            )
        )
        return StepOutcome(trigger=Trigger.VERDICT_REQUIRE_APPROVAL, detail="approval requested")


def _escalate(code: ReviewCode, detail: str) -> StepOutcome:
    return StepOutcome(
        trigger=Trigger.ESCALATE,
        review_reason=ReviewReason(code=code, detail=detail[:300]),
        detail=detail[:300],
    )


def _first_reason(verdict: PolicyVerdict) -> str:
    for result in verdict.results:
        if result.verdict is verdict.verdict:
            return f"{result.rule_id}: {result.reason}"
    return verdict.verdict.name


__all__ = ["NEXT_STEP", "Drafter", "InvestigateStep", "MenuPack", "Policy"]
