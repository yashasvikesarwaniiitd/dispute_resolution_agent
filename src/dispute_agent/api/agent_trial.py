"""`make try-agent`: one synthetic dispute through the line, with the agent deciding (ADR 0029).

The prototype's front door. It runs a named synthetic case from intake to the investigate
station - gather, assess, decide, draft, policy, approval request - with the model-backed
reasoner, and reports what happened in plain words. It is the same line the end-to-end
tests run, assembled here because the long-running runtime gains the investigate station
only with the sending stations in Phase 5 (BACKLOG.md).

**It cannot send anything.** It uses a throwaway database in a temporary folder, the
furthest any case can reach is the approval queue, and no channel is wired at all. The only
thing that leaves the machine is the model call, and that goes through the spending guard
and is logged to `data/model_calls.sqlite3` like any other.

The recipient allowlist is set to the fixture's own synthetic address, because the line's
policy refuses to even draft to an address it does not know (R06) and nothing is sent.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import ActorKind, InboundEventType
from dispute_agent.kernel.domain.events import Event, EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, Step, phase_1_steps
from dispute_agent.kernel.orchestrator.steps import AssessStep, EligibilityStep, GatherEvidenceStep
from dispute_agent.kernel.policy.config import MessagesConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_3_rules
from dispute_agent.kernel.statemachine.machine import StateMachine
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack
from dispute_agent.packs.weight_dispute.render import TemplateDrafter

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import Clock, IdGenerator, Reasoner

MODULES_ON = dict.fromkeys(
    ("email", "ticketing", "ledger", "approvals", "evidence", "reasoning", "model_gateway"),
    True,
)


@dataclass
class Watching:
    """The reasoner, with what it was asked and what it answered kept for the report."""

    inner: Reasoner
    proposals: list[Proposal] = field(default_factory=list)

    def propose(self, briefing: CaseBriefing) -> Proposal:
        proposal = self.inner.propose(briefing)
        self.proposals.append(proposal)
        return proposal


@dataclass(frozen=True)
class TrialReport:
    fixture: str
    case: Case
    proposal: Proposal | None
    fell_back: bool
    email_subject: str | None
    email_body: str | None


def run_trial(
    name: str,
    reasoner: Reasoner,
    *,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
) -> TrialReport:
    built = fixture(name)
    pack = WeightDisputePack()
    now = clock.now()
    facts = CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=now,
        statement=(
            UntrustedText.create(built.statement, "dispute_form", f"form-{name}")
            if built.statement
            else None
        ),
        pack_facts={STATUS_KEY: built.shipment.status},
    )
    policy = policy.model_copy(
        update={
            "messages": MessagesConfig.model_validate(
                {**policy.messages.model_dump(), "allowed_recipients": [built.seller_contact]}
            )
        }
    )
    watching = Watching(reasoner)
    fallback = Watching(RulesOnlyReasoner(pack=pack, ids=ids, clock=clock, fallback_used=True))

    with tempfile.TemporaryDirectory(prefix="try-agent-") as folder:
        engine = create_sqlite_engine(Path(folder) / "trial.sqlite3")
        apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
        try:
            with SqliteUnitOfWork(engine, clock=clock) as work:
                case = _open_case(work, facts, name, now, ids)
                steps: dict[str, Step] = {
                    **phase_1_steps(),
                    "eligibility": EligibilityStep(pack),
                    "gather_evidence": GatherEvidenceStep(
                        pack=pack,
                        sources={"evidence": SyntheticShipmentSource(fixture=built, clock=clock)},
                        store=work.evidence,
                        ids=ids,
                        timers_store=work.timers,
                    ),
                    "assess": AssessStep(pack, work.evidence, work.assessments),
                    "investigate": InvestigateStep(
                        # MenuPack types `state` as object; the pack narrows it to CaseState.
                        # The e2e tests carry the same ignore (Phase 3 BACKLOG item).
                        pack=pack,  # type: ignore[arg-type]
                        reasoner=watching,
                        fallback=fallback,
                        drafter=TemplateDrafter(),
                        policy=PolicyEngine(phase_3_rules(), clock, ids),
                        evidence=work.evidence,
                        assessments=work.assessments,
                        intents=work.intents,
                        approvals=work.approvals,
                        ids=ids,
                        modules=MODULES_ON,
                        config=policy,
                    ),
                }
                advanced = Orchestrator(
                    StateMachine(default_table()),
                    steps,
                    clock,
                    ids,
                    patterns=NullPatternContextProvider(),
                    outcomes=NullOutcomeObserver(),
                ).advance(
                    case[0],
                    case[1],
                    commit=lambda before, after: work.cases.save(after, before.version),
                )
                for event in advanced.events:
                    work.events.append(event)
                pending = list(work.approvals.pending(case[0].id))
                intent = work.intents.get(pending[0].request.intent_id) if pending else None
        finally:
            engine.dispose()

    payload: Any = intent.payload if intent is not None else None
    made = watching.proposals or fallback.proposals
    proposal = made[0] if made else None
    return TrialReport(
        fixture=name,
        case=advanced.case,
        proposal=proposal,
        fell_back=bool(fallback.proposals),
        email_subject=getattr(payload, "subject", None),
        email_body=getattr(payload, "body", None),
    )


def _open_case(
    work: SqliteUnitOfWork, facts: CaseFacts, name: str, now: Any, ids: IdGenerator
) -> tuple[Case, EventId]:
    inbound = InboundEvent(
        type=InboundEventType.DISPUTE_RAISED,
        source="dispute_api",
        source_event_id=f"trial-{name}-{ids.new_id('inb')}",
        occurred_at=now,
        actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        payload=InboundPayload(),
    )
    SqliteInboxStore(work.connection).accept(inbound, now)
    input_id = EventId(ids.new_id("evt"))
    opened = Intake(
        allowed_recipients=(facts.seller_contact,), reopen_window=timedelta(days=7)
    ).open_or_get_case(
        work.cases,
        inbound,
        facts,
        pack="weight_dispute",
        now=now,
        case_id=CaseId(ids.new_id("case")),
        correlation_id=CorrelationId(ids.new_id("corr")),
        opened_event_id=EventId(ids.new_id("evt")),
        causation_id=input_id,
    )
    work.cases.insert(opened.case)
    raised = work.events.append(
        Event(
            id=input_id,
            case_id=opened.case.id,
            type=InboundEventType.DISPUTE_RAISED,
            occurred_at=now,
            recorded_at=now,
            actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
            cause=opened.events[0].cause,
            correlation_id=opened.case.correlation_id,
            dedupe_key=inbound.dedupe_key,
            payload=EventPayload(),
        )
    )
    for event in opened.events:
        work.events.append(event)
    return opened.case, raised.id


__all__ = ["TrialReport", "run_trial"]
