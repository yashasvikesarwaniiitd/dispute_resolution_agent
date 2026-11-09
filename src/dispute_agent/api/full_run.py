"""One synthetic dispute through the whole line, against fakes and the simulated ledger (P5-09).

Intake, evidence, assessment, the decision, your approval, the action, its read-back, the
follow-up and its approval, the outcome. Email and Jira are the Phase 3 fakes (as unkind as
the real ones: delayed visibility, no idempotent writes); the ledger is the real simulated one
in its own file. Approvals are decided by a callback standing in for you, so a test or the
demo can say yes, no, or "not yet".

`open_world` returns the case on the line with its fakes, so a test can go on after the case
rests: a seller reply (`World.reply`), time passing (`clock.advance` then `World.tick`), an
ops finding or a decision through `World.runner.submit`. The steps are assembled by
`api/line.py`, the same assembly the runner and the API use.

The clock is a fake clock: when verification has to wait for Sent Items, the run moves time
forward and advances again, as the real runner would.

Nothing here reaches the network unless the caller passes real adapters in. The live demo
does (`scripts/demo.py` without `--offline`): a real email channel and, if asked, real Jira,
with the owner's test seller address standing in for the fixture's unroutable one. Then the
counts in the report come from the case's own verified actions, not from a fake's memory.
"""

from __future__ import annotations

import dataclasses
import tempfile
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.api.line import Line
from dispute_agent.api.runner import Runner
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ActorKind,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    InternalEventType,
)
from dispute_agent.kernel.domain.events import Event, EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.inbound import SellerReplyReceived
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money, UntrustedText
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.orchestrator import AUTO_STATES
from dispute_agent.kernel.policy.config import MessagesConfig
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
from dispute_agent.modules.email.write_port import MessageWritePort
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from dispute_agent.modules.ledger.write_port import CreditWritePort
from dispute_agent.modules.ticketing.fake.ticketing import FakeTicketingSystem
from dispute_agent.modules.ticketing.write_port import IssueWritePort
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.api.runner import TickReport
    from dispute_agent.infra.clock import FakeClock
    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal
    from dispute_agent.kernel.orchestrator.dispatch import DispatchResult
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import (
        IdGenerator,
        MessageChannel,
        Reasoner,
        ReplyInterpreter,
        TicketingSystem,
    )

REVIEWER = Actor(kind=ActorKind.HUMAN, id="human:owner")
#: How far the clock moves when verification has to wait; above the fakes' visibility delay.
WAIT_STEP = timedelta(seconds=5)


def _yes(_approval: Approval) -> ApprovalDecision | None:
    return ApprovalDecision.APPROVED


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
    #: The run's case database, kept so a test or the demo can read the timeline afterwards.
    database: Path | None = None
    #: The plans still waiting for a decision when the run stopped (what the reviewer sees).
    pending_payloads: list[Any] = field(default_factory=list)


def run_full(
    name: str,
    *,
    policy: PolicyConfig,
    clock: FakeClock,
    ids: IdGenerator,
    reasoner: Reasoner | None = None,
    decide: Callable[[Approval], ApprovalDecision | None] = _yes,
    ledger_failure: str | None = None,
    max_rounds: int = 12,
    modules_off: tuple[str, ...] = (),
    statement: str | None = None,
    notes: tuple[str, ...] | None = None,
    interpreter: ReplyInterpreter | None = None,
    live: Live | None = None,
) -> FullReport:
    """`statement` and `notes` replace the fixture's own (the injection evals, P4-09).

    Raises RecipientNotAllowedError when the fixture's seller is not the test seller: the
    dispute is refused at intake, exactly as the API refuses it (S27).
    """
    world = open_world(
        name,
        policy=policy,
        clock=clock,
        ids=ids,
        reasoner=reasoner,
        decide=decide,
        ledger_failure=ledger_failure,
        modules_off=modules_off,
        statement=statement,
        notes=notes,
        interpreter=interpreter,
        live=live,
    )
    try:
        return world.drive(max_rounds)
    finally:
        world.close()


@dataclass(frozen=True)
class Live:
    """Real adapters for the live demo. Anything left None stays the fake.

    `seller_contact` replaces the fixture's unroutable address with the owner's test seller,
    and becomes the only allowed recipient (I11). `wait_s` is real time to let a real mailbox
    or project show what was just written before the next read-back.
    """

    seller_contact: str
    channel: MessageChannel | None = None
    tickets: TicketingSystem | None = None
    wait_s: float = 2.0


def open_world(
    name: str,
    *,
    policy: PolicyConfig,
    clock: FakeClock,
    ids: IdGenerator,
    reasoner: Reasoner | None = None,
    decide: Callable[[Approval], ApprovalDecision | None] = _yes,
    ledger_failure: str | None = None,
    modules_off: tuple[str, ...] = (),
    statement: str | None = None,
    notes: tuple[str, ...] | None = None,
    interpreter: ReplyInterpreter | None = None,
    evidence_faults: dict[str, str] | None = None,
    kill_switch: Any = None,
    status_known: bool = True,
    live: Live | None = None,
    image_checker: Any = None,
    photos: Any = None,
) -> World:
    """A synthetic dispute opened on the whole line, ready to drive, reply to and tick."""
    built = fixture(name)
    contact = live.seller_contact if live else SELLER_CONTACT
    if live:
        built = dataclasses.replace(built, seller_contact=contact)
    if statement is not None:
        built = dataclasses.replace(built, statement=statement)
    if notes is not None:
        built = dataclasses.replace(built, notes=notes)
    policy = policy.model_copy(
        update={
            "messages": MessagesConfig.model_validate(
                {**policy.messages.model_dump(), "allowed_recipients": [contact]}
            )
        }
    )
    fake_channel = FakeMessageChannel(now=clock.now())
    fake_tickets = FakeTicketingSystem(now=clock.now())
    channel: Any = (live.channel if live else None) or fake_channel
    tickets: Any = (live.tickets if live else None) or fake_tickets
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

    def ports(case: Case, action_type: ActionType) -> Any:
        fake_channel.now = clock.now()
        fake_tickets.now = clock.now()
        if action_type is ActionType.SEND_MESSAGE:
            return MessageWritePort(channel, case.id)
        if action_type is ActionType.UPSERT_ISSUE:
            return IssueWritePort(tickets, case.id, tickets.project_key)
        return CreditWritePort(ledger, case.id)

    source = SyntheticShipmentSource(fixture=built, clock=clock, faults=dict(evidence_faults or {}))
    line = Line(
        pack=WeightDisputePack(),
        policy=policy,
        clock=clock,
        ids=ids,
        ports=ports,
        # Built once: its attempt counter is what makes `timeout_twice` fail, fail, then work.
        sources=lambda: {"evidence": source},
        reasoner=reasoner,
        ledger=ledger,
        interpreter=interpreter,
        modules={module: module not in modules_off for module in _ALL_ON},
        **({"kill_switch": kill_switch} if kill_switch is not None else {}),
        image_checker=image_checker,
        photos=photos,
    )
    engine = create_sqlite_engine(folder / "cases.sqlite3")
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
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
        # The dispute normally carries the delivery status. When it does not
        # (`status_known=False`), eligibility reads it from the evidence source - and an
        # unavailable source is a named escalation, never a guess (S46; contrast S05b).
        pack_facts={STATUS_KEY: built.shipment.status} if status_known else {},
    )
    try:
        with SqliteUnitOfWork(engine, clock=clock) as work:
            case, causation = _open_case(work, facts, name, clock.now(), ids, contact)
    except Exception:
        engine.dispose()
        raise
    return World(
        name=name,
        line=line,
        engine=engine,
        folder=folder,
        clock=clock,
        channel=channel,
        tickets=tickets,
        ledger=ledger,
        case_id=case.id,
        causation=causation,
        decide=decide,
        path=[case.state],
        source=source,
        seller_contact=contact,
        live_wait_s=live.wait_s if live else 0.0,
    )


@dataclass
class World:
    """One case on the whole line, with the fakes it talks to. Close it when done."""

    name: str
    line: Line
    engine: Any
    folder: Path
    clock: FakeClock
    #: The fakes unless the live demo passed real adapters in (`Live`).
    channel: Any
    tickets: Any
    ledger: SimulatedLedger
    case_id: CaseId
    causation: EventId
    decide: Callable[[Approval], ApprovalDecision | None]
    path: list[CaseState] = field(default_factory=list)
    #: The synthetic evidence source, so a scenario can make the ops finding available.
    source: SyntheticShipmentSource | None = None
    seller_contact: str = SELLER_CONTACT
    #: Real seconds to wait before a read-back retry; 0 against the fakes.
    live_wait_s: float = 0.0

    @property
    def runner(self) -> Runner:
        return Runner(self.engine, self.line)

    def drive(self, max_rounds: int = 12) -> FullReport:
        """Advance, answering approvals and waiting out verification, until the case rests."""
        report: FullReport | None = None
        for _ in range(max_rounds):
            with SqliteUnitOfWork(self.engine, clock=self.clock) as work:
                case = work.cases.get(self.case_id)
                for approval in work.approvals.pending(case.id):
                    choice = self.decide(approval)
                    if choice is not None:
                        work.approvals.decide(
                            approval.id, choice, REVIEWER, self.clock.now(), "scripted reviewer"
                        )
                if case.state in AUTO_STATES:
                    history = work.events.list_for_case(case.id)
                    cause = history[-1].id if history else self.causation
                    self.line.advance(work, case, cause, on_save=self._saw)
                report = self._report(work)
                case = report.case
                pending = list(work.approvals.pending(case.id))
            if case.state is CaseState.VERIFYING or (
                case.state is CaseState.AWAITING_APPROVAL and not pending
            ):
                self.clock.advance(WAIT_STEP)
                if self.live_wait_s:
                    time.sleep(self.live_wait_s)
                continue
            if case.state is CaseState.AWAITING_APPROVAL and all(
                self.decide(a) is None for a in pending
            ):
                break
            if case.state not in (CaseState.AWAITING_APPROVAL, CaseState.VERIFYING):
                break
            self.clock.advance(WAIT_STEP)
        assert report is not None
        return report

    def reply(self, text: str, *, message_id: str | None = None) -> DispatchResult:
        """The test seller replies; the subject names the shipment, as a real reply's does."""
        shipment = self.report().case.facts.shipment_ref
        mid = message_id or f"reply-{len(self.path)}-{int(self.clock.now().timestamp())}"
        result = self.runner.submit(
            InboundEvent(
                type=InboundEventType.SELLER_REPLY_RECEIVED,
                source="outlook",
                source_event_id=mid,
                occurred_at=self.clock.now(),
                actor=Actor(kind=ActorKind.EXTERNAL, id="seller"),
                payload=SellerReplyReceived(
                    external_message_id=mid,
                    from_addr=self.seller_contact,
                    subject=UntrustedText.create(f"Re: {shipment}", "seller_email", mid),
                    body=UntrustedText.create(text, "seller_email", mid),
                    received_at=self.clock.now(),
                ),
            )
        )
        self._sync_path()
        return result

    def tick(self) -> TickReport:
        report = self.runner.tick()
        self._sync_path()
        return report

    def report(self) -> FullReport:
        with SqliteUnitOfWork(self.engine, clock=self.clock) as work:
            return self._report(work)

    def close(self) -> None:
        self.engine.dispose()

    def _report(self, work: SqliteUnitOfWork) -> FullReport:
        case = work.cases.get(self.case_id)
        pending = list(work.approvals.pending(case.id))
        verified = [a for a in work.actions.for_case(case.id) if a.status is ActionStatus.VERIFIED]
        sent = [a for a in verified if a.action_type is ActionType.SEND_MESSAGE]
        if isinstance(self.channel, FakeMessageChannel):
            emails = self.channel.sent_count()
            subjects = [s.message.subject for s in self.channel._all]
        else:
            # A real mailbox has its own history; what this run sent is its verified actions.
            emails = len(sent)
            subjects = [_subject(work.intents.get(a.intent_id).payload) for a in sent]
        if isinstance(self.tickets, FakeTicketingSystem):
            issues = self.tickets.issue_count()
        else:
            issues = sum(1 for a in verified if a.action_type is ActionType.UPSERT_ISSUE)
        return FullReport(
            fixture=self.name,
            case=case,
            path=list(self.path),
            approvals=list(work.approvals.for_case(case.id)),
            proposals=list(work.proposals.for_case(case.id)),
            emails_sent=emails,
            jira_issues=issues,
            ledger_credits=self.ledger.credit_count(),
            subjects=subjects,
            database=self.folder / "cases.sqlite3",
            pending_payloads=[work.intents.get(a.request.intent_id).payload for a in pending],
        )

    def _saw(self, saved: Case) -> None:
        self.path.append(saved.state)

    def _sync_path(self) -> None:
        """Every state the case has been in, read back from its trail (I8)."""
        with SqliteUnitOfWork(self.engine, clock=self.clock) as work:
            moved = [
                e.payload.to_state  # type: ignore[attr-defined]
                for e in work.events.list_for_case(self.case_id)
                if e.type is InternalEventType.STATE_CHANGED
            ]
        # T00 (the case opening) has no STATE_CHANGED of its own: RECEIVED leads the path.
        self.path = [CaseState.RECEIVED, *(s for s in moved if s is not CaseState.RECEIVED)]


def _subject(payload: object) -> str:
    return str(getattr(payload, "subject", ""))


def _open_case(
    work: SqliteUnitOfWork,
    facts: CaseFacts,
    name: str,
    now: Any,
    ids: IdGenerator,
    contact: str = SELLER_CONTACT,
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
        allowed_recipients=(contact,), reopen_window=timedelta(days=7)
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


_ALL_ON = dict.fromkeys(
    ("email", "ticketing", "ledger", "approvals", "evidence", "reasoning", "model_gateway"), True
)

__all__ = ["FullReport", "Live", "World", "open_world", "run_full"]
