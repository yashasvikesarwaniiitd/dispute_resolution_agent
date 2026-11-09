"""The production line, assembled once: every step, the effects, the dispatcher.

Steps hold the stores of one unit of work, so they are built per unit of work; everything
long-lived (the pack, the reasoner, the write ports, the clock) is built once and held here.
The trial, the demo, the end-to-end tests and the runner all take their line from this one
place - when they each assembled their own, the runtime quietly ran only the Phase 1 steps
while the tests ran the whole line (BACKLOG, Phase 4+/5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.executor.executor import ActionExecutor
from dispute_agent.kernel.orchestrator.dispatch import Dispatcher
from dispute_agent.kernel.orchestrator.effects import TransitionEffects
from dispute_agent.kernel.orchestrator.execution import ApplyApprovalStep, ExecuteStep, VerifyStep
from dispute_agent.kernel.orchestrator.handoff import HandOffStep
from dispute_agent.kernel.orchestrator.image_check import ImageCheckRound
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, Step, phase_1_steps
from dispute_agent.kernel.orchestrator.reminder import ReminderStep
from dispute_agent.kernel.orchestrator.replies import ReplyReader, contact_status, readings_in
from dispute_agent.kernel.orchestrator.steps import AssessStep, EligibilityStep, GatherEvidenceStep
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_5_rules
from dispute_agent.kernel.statemachine.machine import StateMachine
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider
from dispute_agent.modules.evidence.disabled import DisabledEvidenceSource
from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen
from dispute_agent.modules.reasoning.disabled import DisabledReasoner
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.photo import PHOTO_FLAG, size_facts
from dispute_agent.packs.weight_dispute.render import TemplateDrafter

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.enums import ActionType
    from dispute_agent.kernel.domain.ids import EventId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.orchestrator.orchestrator import AdvanceResult
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import (
        Clock,
        EvidenceSource,
        IdGenerator,
        Ledger,
        Reasoner,
        ReplyInterpreter,
    )
    from dispute_agent.packs.weight_dispute.pack import WeightDisputePack


class _Off:
    def is_engaged(self) -> bool:
        return False


@dataclass
class Line:
    pack: WeightDisputePack
    policy: PolicyConfig
    clock: Clock
    ids: IdGenerator
    #: The write port for an action on a case (email, Jira, ledger).
    ports: Callable[[Case, ActionType], Any]
    #: The evidence sources by module name.
    sources: Callable[[], Mapping[str, EvidenceSource]]
    reasoner: Reasoner | None = None
    ledger: Ledger | None = None
    interpreter: ReplyInterpreter | None = None
    modules: Mapping[str, bool] = field(default_factory=dict)
    kill_switch: Any = field(default_factory=_Off)
    #: Spans for every step (observability.md 3); None: untraced, as the Tracer port allows.
    tracer: Any = None
    #: HOOK A: the PatternContextProvider (Null until cross_case is on, ADR 0035).
    patterns: Any = None
    #: ADR 0049: the image check and the photos it reads. Both None: no image check.
    image_checker: Any = None
    photos: Any = None

    def machine(self) -> StateMachine:
        return StateMachine(default_table())

    def steps(self, work: SqliteUnitOfWork) -> dict[str, Step]:
        rules = PolicyEngine(phase_5_rules(), self.clock, self.ids)
        executor = ActionExecutor(
            PolicyEngine(phase_5_rules(), self.clock, self.ids), work.actions, self.kill_switch
        )
        baseline = RulesOnlyReasoner(pack=self.pack, ids=self.ids, clock=self.clock)
        reasoning_on = self.modules.get("reasoning", True)
        # Reasoning switched off: the line has no reasoner, and the baseline decides what it
        # can; when it cannot, the case goes to a person naming the module (I6, S29a/b).
        reasoner = (self.reasoner or baseline) if reasoning_on else DisabledReasoner()
        fallback = (
            None
            if reasoner is baseline
            else RulesOnlyReasoner(
                pack=self.pack, ids=self.ids, clock=self.clock, fallback_used=True
            )
        )
        drafter = TemplateDrafter()
        return {
            **phase_1_steps(),
            "eligibility": EligibilityStep(self.pack, facts=self._eligibility_facts),
            "gather_evidence": GatherEvidenceStep(
                pack=self.pack,
                sources=(
                    self.sources()
                    if self.modules.get("evidence", True)
                    else {"evidence": DisabledEvidenceSource()}
                ),
                store=work.evidence,
                ids=self.ids,
                timers_store=work.timers,
                timers=self.policy.timers,
                image_check=(
                    ImageCheckRound(
                        events=work.events,
                        evidence=work.evidence,
                        ids=self.ids,
                        checker=self.image_checker,
                        photos=self.photos,
                        image_key=PHOTO_FLAG,
                        size_facts=size_facts,
                    )
                    if self.image_checker is not None and self.photos is not None
                    else None
                ),
                replies=ReplyReader(
                    messages=work.messages,
                    inbox=work.inbox,
                    evidence=work.evidence,
                    proposals=work.proposals,
                    ids=self.ids,
                    interpreter=self.interpreter if reasoning_on else None,
                ),
            ),
            "assess": AssessStep(self.pack, work.evidence, work.assessments),
            "investigate": InvestigateStep(
                pack=self.pack,  # type: ignore[arg-type]
                reasoner=reasoner,
                fallback=fallback,
                screen=RulesBasedInputScreen(),
                drafter=drafter,
                policy=rules,
                evidence=work.evidence,
                assessments=work.assessments,
                intents=work.intents,
                approvals=work.approvals,
                ids=self.ids,
                modules=self.modules,
                config=self.policy,
                proposals=work.proposals,
                actions=work.actions,
                ledger=self.ledger,
                executor=executor,
                readings=lambda case: readings_in(work.evidence.bundle(case.id, case.gather_round)),
                contact=lambda case, bundle: contact_status(
                    case, bundle, work.actions, work.intents, work.timers
                ),
                patterns=self.patterns,
            ),
            "apply_approval": ApplyApprovalStep(
                intents=work.intents,
                approvals=work.approvals,
                actions=work.actions,
                executor=executor,
                ids=self.ids,
                ledger=self.ledger,
                modules=self.modules,
                config=self.policy,
                assessments=work.assessments,
            ),
            "execute": ExecuteStep(
                intents=work.intents,
                actions=work.actions,
                executor=executor,
                ports=self.ports,
                checkpoint=work.checkpoint,
            ),
            "verify": VerifyStep(
                intents=work.intents,
                actions=work.actions,
                executor=executor,
                ports=self.ports,
                policy=rules,
                approvals=work.approvals,
                drafter=drafter,
                ids=self.ids,
                assessments=work.assessments,
                modules=self.modules,
                config=self.policy,
            ),
            "hand_off": HandOffStep(
                drafter=drafter,
                policy=rules,
                assessments=work.assessments,
                intents=work.intents,
                approvals=work.approvals,
                ids=self.ids,
                executor=executor,
                modules=self.modules,
                config=self.policy,
            ),
            "reminder": ReminderStep(
                drafter=drafter,
                policy=rules,
                proposals=work.proposals,
                assessments=work.assessments,
                intents=work.intents,
                approvals=work.approvals,
                ids=self.ids,
                executor=executor,
                modules=self.modules,
                config=self.policy,
            ),
        }

    def _eligibility_facts(self, case: Case) -> CaseFacts:
        """The delivery status from the evidence source, when the dispute did not carry it."""
        facts = case.facts
        if STATUS_KEY in facts.pack_facts:
            return facts
        requirement = next(
            (r for r in self.pack.evidence_plan(facts) if r.key == "shipment.status"), None
        )
        source = (
            self.sources().get("evidence")
            if self.modules.get("evidence", True)
            else DisabledEvidenceSource()
        )
        if requirement is None or source is None:
            return facts
        result = source.fetch(case.id, facts, requirement)  # ModuleUnavailable propagates
        status = (result.value or {}).get("status") if result.status.value == "FOUND" else None
        if status is None:
            return facts  # unknown stays unknown: eligibility says so, a person decides
        return facts.model_copy(update={"pack_facts": {**facts.pack_facts, STATUS_KEY: status}})

    def orchestrator(self, work: SqliteUnitOfWork) -> Orchestrator:
        return Orchestrator(
            self.machine(),
            self.steps(work),
            self.clock,
            self.ids,
            patterns=NullPatternContextProvider(),
            outcomes=NullOutcomeObserver(),
            max_steps=self.policy.cases.max_steps_per_advance,
            record=work.events.append,
            tracer=self.tracer,
            effects=TransitionEffects(
                timers=work.timers,
                intents=work.intents,
                approvals=work.approvals,
                actions=work.actions,
                ids=self.ids,
                config=self.policy,
                outcomes=work.outcomes,
            ),
        )

    def dispatcher(self, work: SqliteUnitOfWork) -> Dispatcher:
        return Dispatcher(
            orchestrator=self.orchestrator(work),
            machine=self.machine(),
            inbox=work.inbox,
            cases=work.cases,
            events=work.events,
            timers=work.timers,
            intents=work.intents,
            approvals=work.approvals,
            actions=work.actions,
            messages=work.messages,
            clock=self.clock,
            ids=self.ids,
            config=self.policy,
        )

    def advance(
        self,
        work: SqliteUnitOfWork,
        case: Case,
        causation: EventId,
        on_save: Callable[[Case], None] | None = None,
    ) -> AdvanceResult:
        """Advance a case, saving per transition and writing every event it produced."""

        def commit(before: Case, after: Case) -> Case:
            saved: Case = work.cases.save(after, before.version)
            if on_save is not None:
                on_save(saved)
            return saved

        result = self.orchestrator(work).advance(case, causation, commit=commit)
        # Events are written as each move lands (Orchestrator `record`). A move may release
        # events held for its new state - redeliver them now (orchestrator.md 3.4, review M1).
        if result.case.state is not case.state:
            self.dispatcher(work).redeliver(case.id)
        return result


__all__ = ["Line"]
