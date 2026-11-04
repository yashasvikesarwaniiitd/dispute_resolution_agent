"""One synthetic dispute through the whole line, against fakes and the simulated ledger (P5-09).

Intake, evidence, assessment, the decision, your approval, the action, its read-back, the
follow-up and its approval, the outcome. Email and Jira are the Phase 3 fakes (as unkind as
the real ones: delayed visibility, no idempotent writes); the ledger is the real simulated one
in its own file. Approvals are decided by a callback standing in for you, so a test or the
demo can say yes, no, or "not yet".

The clock is a fake clock: when verification has to wait for Sent Items, the run moves time
forward and advances again, as the real runner would after its retry timer.

Nothing here reaches the network unless the caller passes real adapters in.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.api.agent_trial import _open_case
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import ActionType, ActorKind, ApprovalDecision, CaseState
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money, UntrustedText
from dispute_agent.kernel.executor.executor import ActionExecutor
from dispute_agent.kernel.orchestrator.execution import ApplyApprovalStep, ExecuteStep, VerifyStep
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, Step, phase_1_steps
from dispute_agent.kernel.orchestrator.steps import AssessStep, EligibilityStep, GatherEvidenceStep
from dispute_agent.kernel.policy.config import MessagesConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_5_rules
from dispute_agent.kernel.statemachine.machine import StateMachine
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
from dispute_agent.modules.email.write_port import MessageWritePort
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.modules.input_screen.rules_based import RulesBasedInputScreen
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from dispute_agent.modules.ledger.write_port import CreditWritePort
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.modules.ticketing.fake.ticketing import FakeTicketingSystem
from dispute_agent.modules.ticketing.write_port import IssueWritePort
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack
from dispute_agent.packs.weight_dispute.render import TemplateDrafter

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.infra.clock import FakeClock
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import IdGenerator, Reasoner

REVIEWER = Actor(kind=ActorKind.HUMAN, id="human:owner")
#: How far the clock moves when verification has to wait; above the fakes' visibility delay.
WAIT_STEP = timedelta(seconds=5)


class _Off:
    def is_engaged(self) -> bool:
        return False


@dataclass
class FullReport:
    fixture: str
    case: Case
    path: list[CaseState] = field(default_factory=list)
    approvals: list[Approval] = field(default_factory=list)
    proposals: list[Proposal] = field(default_factory=list)
    emails_sent: int = 0
    jira_issues: int = 0
    ledger_credits: int = 0
    subjects: list[str] = field(default_factory=list)


def run_full(
    name: str,
    *,
    policy: PolicyConfig,
    clock: FakeClock,
    ids: IdGenerator,
    reasoner: Reasoner | None = None,
    decide: Callable[[Approval], ApprovalDecision | None] = lambda _a: ApprovalDecision.APPROVED,
    ledger_failure: str | None = None,
    max_rounds: int = 12,
) -> FullReport:
    built = fixture(name)
    pack = WeightDisputePack()
    policy = policy.model_copy(
        update={
            "messages": MessagesConfig.model_validate(
                {**policy.messages.model_dump(), "allowed_recipients": [SELLER_CONTACT]}
            )
        }
    )
    channel = FakeMessageChannel(now=clock.now())
    tickets = FakeTicketingSystem(now=clock.now())
    folder = Path(tempfile.mkdtemp(prefix="full-run-"))
    ledger = SimulatedLedger(
        folder / "ledger.sqlite3", failure_mode=ledger_failure, clock=clock.now
    )
    for entry in built.shipment.ledger:
        if entry.kind == "DEBIT":
            ledger.seed_debit(
                entry.shipment_ref,
                Money.from_paise(entry.amount_paise),
                entry.reason_code or "weight_charge",
                entry.transaction_id,
            )
    the_reasoner = reasoner or RulesOnlyReasoner(pack=pack, ids=ids, clock=clock)

    def ports(case: Case, action_type: ActionType) -> Any:
        channel.now = clock.now()
        tickets.now = clock.now()
        if action_type is ActionType.SEND_MESSAGE:
            return MessageWritePort(channel, case.id)
        if action_type is ActionType.UPSERT_ISSUE:
            return IssueWritePort(tickets, case.id, tickets.project_key)
        return CreditWritePort(ledger, case.id)

    engine = create_sqlite_engine(folder / "cases.sqlite3")
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    report: FullReport | None = None
    facts = CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=clock.now(),
        statement=(
            UntrustedText.create(built.statement, "dispute_form", f"form-{name}")
            if built.statement
            else None
        ),
        pack_facts={STATUS_KEY: built.shipment.status},
    )
    try:
        with SqliteUnitOfWork(engine, clock=clock) as work:
            case, causation = _open_case(work, facts, name, clock.now(), ids)
        path = [case.state]

        for _ in range(max_rounds):
            with SqliteUnitOfWork(engine, clock=clock) as work:
                case = work.cases.get(case.id)
                for approval in work.approvals.pending(case.id):
                    choice = decide(approval)
                    if choice is not None:
                        work.approvals.decide(
                            approval.id, choice, REVIEWER, clock.now(), "scripted reviewer"
                        )
                executor = ActionExecutor(
                    PolicyEngine(phase_5_rules(), clock, ids), work.actions, _Off()
                )
                engine_policy = PolicyEngine(phase_5_rules(), clock, ids)
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
                        pack=pack,  # type: ignore[arg-type]
                        reasoner=the_reasoner,
                        fallback=RulesOnlyReasoner(
                            pack=pack, ids=ids, clock=clock, fallback_used=True
                        ),
                        screen=RulesBasedInputScreen(),
                        drafter=TemplateDrafter(),
                        policy=engine_policy,
                        evidence=work.evidence,
                        assessments=work.assessments,
                        intents=work.intents,
                        approvals=work.approvals,
                        ids=ids,
                        modules=_ALL_ON,
                        config=policy,
                        proposals=work.proposals,
                        actions=work.actions,
                        ledger=ledger,
                        executor=executor,
                    ),
                    "apply_approval": ApplyApprovalStep(
                        intents=work.intents,
                        approvals=work.approvals,
                        actions=work.actions,
                        executor=executor,
                        ids=ids,
                        ledger=ledger,
                        modules=_ALL_ON,
                        config=policy,
                        assessments=work.assessments,
                    ),
                    "execute": ExecuteStep(
                        intents=work.intents,
                        actions=work.actions,
                        executor=executor,
                        ports=ports,
                    ),
                    "verify": VerifyStep(
                        intents=work.intents,
                        actions=work.actions,
                        executor=executor,
                        ports=ports,
                        policy=engine_policy,
                        approvals=work.approvals,
                        drafter=TemplateDrafter(),
                        ids=ids,
                        assessments=work.assessments,
                        modules=_ALL_ON,
                        config=policy,
                    ),
                }
                saved: list[CaseState] = []

                def commit(before: Case, after: Case, _w: Any = work, _s: Any = saved) -> Case:
                    _s.append(after.state)
                    result: Case = _w.cases.save(after, before.version)
                    return result

                advanced = Orchestrator(
                    StateMachine(default_table()),
                    steps,
                    clock,
                    ids,
                    patterns=NullPatternContextProvider(),
                    outcomes=NullOutcomeObserver(),
                    max_steps=policy.cases.max_steps_per_advance,
                ).advance(case, causation, commit=commit)
                for event in advanced.events:
                    work.events.append(event)
                path.extend(saved)
                case = advanced.case
                pending = list(work.approvals.pending(case.id))
                report = FullReport(
                    fixture=name,
                    case=case,
                    path=list(path),
                    approvals=list(work.approvals.for_case(case.id)),
                    proposals=list(work.proposals.for_case(case.id)),
                    emails_sent=channel.sent_count(),
                    jira_issues=tickets.issue_count(),
                    ledger_credits=ledger.credit_count(),
                    subjects=[s.message.subject for s in channel._all],
                )
            if case.state is CaseState.VERIFYING or (
                case.state is CaseState.AWAITING_APPROVAL and not pending
            ):
                clock.advance(WAIT_STEP)
                continue
            if case.state is CaseState.AWAITING_APPROVAL and all(
                decide(a) is None for a in pending
            ):
                break
            if case.state not in (CaseState.AWAITING_APPROVAL, CaseState.VERIFYING):
                break
            clock.advance(WAIT_STEP)
    finally:
        engine.dispose()
    assert report is not None
    return report


_ALL_ON = dict.fromkeys(
    ("email", "ticketing", "ledger", "approvals", "evidence", "reasoning", "model_gateway"), True
)

__all__ = ["FullReport", "run_full"]
