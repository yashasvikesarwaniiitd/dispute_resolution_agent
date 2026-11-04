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
    CreditPayload,
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
    ScreenKind,
    Trigger,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    BudgetExceeded,
    DomainValidationError,
    GuardrailBlocked,
    InvalidModelOutput,
    KillSwitchEngaged,
    ModuleUnavailable,
    NotFound,
    PermanentPortError,
    PolicyRefused,
    PortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, ApprovalId, IntentId
from dispute_agent.kernel.domain.models import ReviewReason
from dispute_agent.kernel.domain.reasoning import (
    CaseBriefing,
    ContactStatus,
    DraftContext,
    ScreenResult,
)
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.policy.credit import credit_amount
from dispute_agent.kernel.policy.verdicts import PolicyContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, ActionPayload, PolicyVerdict
    from dispute_agent.kernel.domain.evidence import ChargeAssessment
    from dispute_agent.kernel.domain.ids import EvidenceItemId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import DraftedMessage, Proposal, ReplyReading
    from dispute_agent.kernel.domain.values import UntrustedText
    from dispute_agent.kernel.ports import (
        ApprovalStore,
        AssessmentStore,
        EvidenceStore,
        IdGenerator,
        InputScreen,
        IntentStore,
        Ledger,
        ProposalStore,
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


class CreditedOnShipment(Protocol):
    def credited_on_shipment(self, shipment_ref: str) -> tuple[Money, Money]: ...


class Planner(Protocol):
    """The executor's planning half: re-check policy at EXECUTION, write the PLANNED row."""

    def plan(
        self,
        case: Case,
        intent: ActionIntent,
        ctx: PolicyContext,
        now: datetime,
        action_id: ActionId,
    ) -> tuple[Action, PolicyVerdict]: ...


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
    #: Reads every outside text of the round before the reasoner is asked (orchestrator.md
    #: section 3, step 1). Advisory (I15): its flags reach the briefing and policy, and only
    #: a provider block changes the path - to a person.
    screen: InputScreen | None = None
    #: The reply readings of this round, when a reply round is running (R20, R21).
    readings: Callable[[Case], tuple[ReplyReading, ...]] | None = None
    #: Every proposal is kept, with its prompt and model, before anything acts on it (I8).
    proposals: ProposalStore | None = None
    #: Where credits already planned or posted are counted from (R15/R16 netting).
    actions: CreditedOnShipment | None = None
    #: The ledger, read for what was charged (R16). None: the limit is unknown, so refused.
    ledger: Ledger | None = None
    #: Plans an ALLOWed action (T06). None: an ALLOW is escalated, as before Phase 5.
    executor: Planner | None = None
    name: str = "investigate"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        assessment = self.assessments.latest(case.id)
        if assessment is None:
            # assess always writes one before this step can be reached. If it is missing,
            # something upstream is broken, and deciding without the numbers is the one
            # thing this step must never do.
            return _escalate(ReviewCode.INTERNAL_ERROR, "no assessment for this round")

        briefing = self._briefing(case, assessment)
        screened = self._screen(briefing)
        if isinstance(screened, StepOutcome):
            return screened
        briefing = briefing.model_copy(update={"screen_results": screened})
        decided = self._propose(briefing)
        if isinstance(decided, StepOutcome):
            return decided
        proposal = decided
        if self.proposals is not None:
            # Kept before anything is checked or done with it, so a refused proposal is on
            # the record too - "why did the case go to a person" needs it most.
            self.proposals.add(proposal)

        # The menu is code's decision, not the reasoner's (I1). Before this check the menu
        # was built, put on the briefing and then ignored, so an off-menu proposal - the
        # kernel rule that removes uphold_charge and propose_credit once money has moved
        # rests on exactly this - would have been acted on (second review, finding 3).
        if proposal.action not in briefing.allowed_actions:
            return _escalate(
                ReviewCode.INVALID_MODEL_OUTPUT,
                f"proposed {proposal.action.value}, which is not on this case's menu",
            )
        return self._act_on(
            case, proposal, assessment, now, briefing.allowed_actions, briefing.screen_results
        )

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
            # policy-rules.md section 8: M01, M03/M05 and M04 each have their own code.
            code = {
                "credit_cap_reached": ReviewCode.CREDIT_CAP_REACHED,
                "credit_window_ended": ReviewCode.CREDIT_WINDOW_ENDED,
            }.get(budget.kind, ReviewCode.LLM_BUDGET_EXCEEDED)
            return _escalate(code, str(budget))
        except GuardrailBlocked as blocked:
            return _escalate(ReviewCode.GUARDRAIL_BLOCKED, str(blocked))
        except AgentDrift as drift:
            return _escalate(ReviewCode.AGENT_DRIFT, str(drift))
        except PermanentPortError as refused:
            # 401/403, 410 Gone, a refused request: retrying or falling back cannot fix a
            # credential or a retired model, and the owner has to look (foundry.md section 7).
            return StepOutcome(
                trigger=Trigger.ESCALATE,
                review_reason=ReviewReason(
                    code=ReviewCode.MODULE_UNAVAILABLE,
                    module="model_gateway",
                    detail=f"the model route refused the call ({refused.code})",
                ),
                detail=f"model_refused: {refused.code}",
            )
        except (DomainValidationError, NotFound) as broken:
            # The prompt could not be loaded or rendered. A configuration fault, normally
            # caught at start-up; if it reaches a case, a person looks (review, M4).
            return _escalate(ReviewCode.INTERNAL_ERROR, f"prompt: {broken}")

    # -- screening ---------------------------------------------------------------------------

    def _screen(self, briefing: CaseBriefing) -> tuple[ScreenResult, ...] | StepOutcome:
        """Every outside text of the round, screened before any model sees it (I15)."""
        if self.screen is None:
            return ()
        results: list[ScreenResult] = []
        for text, kind, item_id in _outside_texts(briefing):
            result = self.screen.screen(text, kind)
            results.append(result.model_copy(update={"evidence_item_id": item_id}))
        if any(r.blocked for r in results):
            # R23: a provider block is a person's call, and never fails over.
            return _escalate(ReviewCode.GUARDRAIL_BLOCKED, "an outside text was blocked")
        return tuple(results)

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
        screens: tuple[ScreenResult, ...] = (),
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

        credited: tuple[Money, Money] | None = None
        debited: Money | None = None
        if action is ProposalAction.PROPOSE_CREDIT:
            credited, debited = self._credit_inputs(case)
            amount = (
                None
                if credited is None
                else credit_amount(
                    assessment,
                    proposal.action_parameters.reason_code or "",
                    credited[0],
                    credited[1],
                )
            )
            if amount is None:
                if len(assessment.definite_errors) > 1:
                    return _escalate(
                        ReviewCode.MULTIPLE_DEFINITE_ERRORS,
                        "the assessment proves more than one error; one credit per case",
                    )
                if credited is None:
                    return _escalate(
                        ReviewCode.LEDGER_STATE_UNKNOWN,
                        "earlier credits on this shipment could not be counted",
                    )
                return _escalate(
                    ReviewCode.POLICY_DENIED,
                    f"credit_reason_unproven: {proposal.action_parameters.reason_code!r} "
                    "is not a code-proven error with an amount still owed",
                )
            payload: ActionPayload = CreditPayload(
                shipment_ref=case.facts.shipment_ref,
                # Derived by code from the assessment (money-rules.md section 3), never from
                # the proposal: the model chose to credit, code decided how much (I9).
                amount=amount,
                reason_code=proposal.action_parameters.reason_code or "",
                fact_ids=tuple(
                    f.fact_id
                    for f in assessment.facts
                    if f.fact_id in ("f_charged", "f_recalculated", "f_discrepancy")
                ),
            )
            return self._plan(
                case, proposal, assessment, now, menu, screens, payload, credited, debited
            )

        try:
            payload = self._payload(case, proposal, assessment)
        except DomainValidationError as blocked:
            # A template that cannot be rendered - a missing fact, or a message kind with no
            # template yet - is a draft that must not go out (orchestrator.md 3.6).
            return _escalate(ReviewCode.POLICY_ESCALATED, f"draft_blocked: {blocked}")

        return self._plan(case, proposal, assessment, now, menu, screens, payload, None, None)

    def _plan(
        self,
        case: Case,
        proposal: Proposal,
        assessment: ChargeAssessment,
        now: datetime,
        menu: tuple[ProposalAction, ...],
        screens: tuple[ScreenResult, ...],
        payload: ActionPayload,
        credited: tuple[Money, Money] | None,
        debited: Money | None,
    ) -> StepOutcome:
        action_type = (
            ActionType.SEND_MESSAGE
            if isinstance(payload, MessagePayload)
            else ActionType.POST_CREDIT
            if isinstance(payload, CreditPayload)
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

        ctx = PolicyContext(
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
            screen_results=screens,
            reply_readings=self.readings(case) if self.readings else (),
            credited_on_shipment=None if credited is None else credited[0],
            credited_duplicate_on_shipment=None if credited is None else credited[1],
            debited_on_shipment=debited,
        )
        return self._by_verdict(case, intent, self.policy.evaluate(ctx), now, ctx)

    def _credit_inputs(self, case: Case) -> tuple[tuple[Money, Money] | None, Money | None]:
        """(already credited on the shipment, the duplicate part), and what was debited.

        Unknown stays unknown: a store or ledger that cannot be read gives None, and R15/R16
        refuse rather than treat it as zero (money-rules.md section 5).
        """
        shipment = case.facts.shipment_ref
        credited = None if self.actions is None else self.actions.credited_on_shipment(shipment)
        debited: Money | None = None
        if self.ledger is not None:
            try:
                paise = sum(
                    e.amount.to_paise()
                    for e in self.ledger.get_entries(shipment)
                    if e.type == "DEBIT"
                )
                debited = Money.from_paise(paise)
            except (PortError, ModuleUnavailable):
                debited = None
        return credited, debited

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
        self,
        case: Case,
        intent: ActionIntent,
        verdict: PolicyVerdict,
        now: datetime,
        ctx: PolicyContext,
    ) -> StepOutcome:
        kind = verdict.verdict
        if kind is VerdictKind.DENY:
            reason = _first_reason(verdict)
            return _escalate(_review_code_for(reason, ReviewCode.POLICY_DENIED), reason)
        if kind is VerdictKind.ESCALATE:
            reason = _first_reason(verdict)
            return _escalate(_review_code_for(reason, ReviewCode.POLICY_ESCALATED), reason)
        if kind is VerdictKind.ALLOW:
            if self.executor is None or intent.action_type is ActionType.POST_CREDIT:
                # A credit can never be ALLOWed (R13); if one were, stop (I3).
                return _escalate(
                    ReviewCode.POLICY_ESCALATED,
                    f"{intent.action_type.value} was allowed but cannot be planned here",
                )
            planned_intent = intent.model_copy(update={"status": IntentStatus.PLANNED})
            self.intents.add(planned_intent)
            try:
                action, _ = self.executor.plan(
                    case, planned_intent, ctx, now, ActionId(self.ids.new_id("act"))
                )
            except (PolicyRefused, KillSwitchEngaged) as refused:
                return _escalate(ReviewCode.POLICY_DENIED, f"execution re-check: {refused}")
            return StepOutcome(
                trigger=Trigger.VERDICT_ALLOW,
                guards={"intent_is_not_credit": True, "action_planned": True},
                detail="action planned",
                pending_intent_id=planned_intent.id,
                action_seq=action.action_seq,
            )

        # REQUIRE_APPROVAL: write the plan, then the request that points at it. The approval
        # binds to this hash, so the text cannot change between here and your yes (I13).
        self.intents.add(intent)
        is_credit = intent.action_type is ActionType.POST_CREDIT
        self.approvals.add(
            ApprovalRequest(
                approval_id=ApprovalId(self.ids.new_id("apr")),
                case_id=case.id,
                intent_id=intent.id,
                subject=ApprovalSubject.CREDIT if is_credit else ApprovalSubject.MESSAGE,
                payload_hash=intent.payload_hash,
                # A credit approval names its amount: the reviewer approves *this* figure,
                # and the executor refuses any other (I3).
                amount=getattr(intent.payload, "amount", None) if is_credit else None,
                message_kind=None if is_credit else getattr(intent.payload, "kind", None),
                requested_at=now,
                expires_at=now + timedelta(hours=self.config.approvals.expire_after_hours),
            )
        )
        return StepOutcome(
            trigger=Trigger.VERDICT_REQUIRE_APPROVAL,
            detail="approval requested",
            pending_intent_id=intent.id,
        )


#: A rule's reason prefix -> the review code a person sees (policy-rules.md section 8).
#: A specific code tells the reviewer what to look at; "policy escalated" does not.
_CODE_FOR_REASON = {
    "manipulation_suspected": ReviewCode.MANIPULATION_SUSPECTED,
    "seller_withdrawal_claimed": ReviewCode.SELLER_WITHDRAWAL_CLAIMED,
    "low_confidence": ReviewCode.LOW_CONFIDENCE,
    "guardrail_blocked": ReviewCode.GUARDRAIL_BLOCKED,
    "multiple_definite_errors": ReviewCode.MULTIPLE_DEFINITE_ERRORS,
    "ledger_state_unknown": ReviewCode.LEDGER_STATE_UNKNOWN,
}


def _review_code_for(reason: str, default: ReviewCode) -> ReviewCode:
    """`reason` is "R21: manipulation_suspected: ..."; the code follows the rule's reason."""
    after_rule = reason.split(": ", 1)[-1]
    for prefix, code in _CODE_FOR_REASON.items():
        if after_rule.startswith(prefix):
            return code
    return default


def _outside_texts(
    briefing: CaseBriefing,
) -> list[tuple[UntrustedText, ScreenKind, EvidenceItemId | None]]:
    out: list[tuple[UntrustedText, ScreenKind, EvidenceItemId | None]] = []
    if briefing.facts.statement is not None:
        out.append((briefing.facts.statement, ScreenKind.DISPUTE_STATEMENT, None))
    kinds = {
        "seller_email": ScreenKind.SELLER_REPLY,
        "seller_portal": ScreenKind.SELLER_REPLY,
        "jira_comment": ScreenKind.OPS_NOTE,
        "shipment_note": ScreenKind.SHIPMENT_NOTE,
        "dispute_form": ScreenKind.DISPUTE_STATEMENT,
    }
    for item in briefing.evidence.items:
        for text in item.result.untrusted:
            out.append((text, kinds.get(text.origin, ScreenKind.SELLER_REPLY), item.id))
    return out


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
