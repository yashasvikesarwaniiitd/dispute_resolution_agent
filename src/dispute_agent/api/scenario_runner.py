"""Plays one catalogue scenario on the whole line and reports what happened (P6-01).

The script's steps become what the outside world would send: approvals and rejections as
APPROVAL_DECIDED events bound to the text the reviewer saw, replies and findings and
withdrawals through dispatch, timers by moving the fake clock and ticking the runner, the kill
switch as a switch the executor reads. Nothing is stepped around: the case goes through the same
dispatcher, policy, executor and verification as in the runtime.

The model's choices are recorded answers (`modules/evals/scenarios/answers.py`) behind the real
spending guard, so a call limit, a failover or a guardrail block happens for real.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.api.full_run import open_world
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    ActorKind,
    ApprovalSubject,
    CaseState,
    InboundEventType,
    InternalEventType,
    TimerKind,
)
from dispute_agent.kernel.domain.errors import AgentDrift, DuplicateEvent
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.external import OpsFinding
from dispute_agent.kernel.domain.inbound import (
    ApprovalDecided,
    DeliveryFailureReceived,
    DisputeWithdrawn,
    HumanConfirmedWithdrawal,
    HumanReopenedCase,
    HumanReturnedCase,
    HumanTookCase,
    OpsFindingReceived,
    UnknownActionResolution,
    parse_inbound,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.kernel.orchestrator.intake import RecipientNotAllowedError
from dispute_agent.kernel.policy.config import LlmConfig, PolicyConfig
from dispute_agent.modules.evals.scenarios import answers
from dispute_agent.modules.evals.scenarios.checks import Outcome, Result, check
from dispute_agent.modules.evidence.synthetic_shipments import fixtures as bank

if TYPE_CHECKING:
    from dispute_agent.api.full_run import World
    from dispute_agent.kernel.domain.reasoning import ModelRequest
    from dispute_agent.modules.evals.scenarios.catalogue import Scenario, Step

START = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)
OWNER = Actor(kind=ActorKind.HUMAN, id="human:owner")
PROMPTS = Path(__file__).resolve().parents[3] / "prompts"

#: Catalogue fault names -> what the fakes call them.
EMAIL_FAULTS = {
    "timeout_after_send": "timeout",
    "timeout_before_move": "stuck_in_drafts",
    "error_400": "permanent",
}
EVIDENCE_KEYS = {
    "evidence.measurements": "shipment.measured_dims",
    "evidence.shipment_status": "shipment.status",
}
FINDING_KIND = {
    "remeasured_lower": "remeasured",
    "no_fault_found": "no_fault_found",
    "inconclusive_still_checking": "inspected",
    "with_injected_note": "no_fault_found",
}


@dataclass
class Switch:
    """The kill switch, as the executor reads it (I10)."""

    on: bool = False

    def is_engaged(self) -> bool:
        return self.on


@dataclass
class ScriptedModel:
    """The recorded answers: next expected action for the investigator, a reading per reply."""

    actions: list[str]
    fixture: str | None
    fault: str | None = None
    calls: list[str] = field(default_factory=list)

    def __call__(self, req: ModelRequest) -> str | dict[str, object]:
        self.calls.append(req.purpose)
        text = " ".join(m.content for m in req.prompt.messages)
        if self.fault == "agent_definition_drift":
            raise AgentDrift("the hosted agent's definition differs from the lock")
        if req.purpose == "seller_reply_interpreter":
            label = (answers.LABEL.search(text) or _NO_LABEL).group(1)
            if self.fault == "content_filter_block" and label in ("jailbreak",):
                return "guardrail"
            return answers.reading(label)
        if not self.actions:
            return answers.investigator("escalate_to_human", self.fixture)
        return answers.investigator(self.actions.pop(0), self.fixture)


class _NoLabel:
    def group(self, _n: int) -> str:
        return "any"


_NO_LABEL = _NoLabel()


@dataclass(frozen=True)
class ScenarioModels:
    """What the composition root builds for a scenario: the guarded path and its users."""

    gateway: Any
    reasoner: Any
    interpreter: Any
    secondary: Any = None


#: (recorded answers, policy, clock, ids) -> the guarded model path. Built in app.py only
#: (tests/architecture/test_guarded_model_path.py): no other module constructs a model route.
ModelFactory = Callable[[Any, PolicyConfig, Any, Any], ScenarioModels]


@dataclass
class Played:
    world: World
    model: ScriptedModel | None
    gateway: Any
    secondary: Any
    notes: list[str] = field(default_factory=list)


def play(
    scenario: Scenario,
    actions: list[str] | None = None,
    models: ModelFactory | None = None,
) -> Result:
    """Run one scenario and check it against what it declares.

    `actions` are the recorded investigator answers in order (`catalogue.recorded_actions`,
    which puts an included script's rounds first); by default the scenario's own.
    """
    try:
        played = _run(scenario, actions, models)
    except RecipientNotAllowedError:
        # Refused at the door (I11, S27): no case, no event, nothing to drive.
        return check(scenario.id, scenario.expect, Outcome(final=None, counts={"cases": 0}))
    except Exception as failed:  # a crash is a failed scenario, never a skipped one
        return check(
            scenario.id, scenario.expect, Outcome(error=f"{type(failed).__name__}: {failed}")
        )
    try:
        outcome = _outcome(played)
    finally:
        played.world.close()
    return check(scenario.id, scenario.expect, outcome)


def _run(
    scenario: Scenario,
    actions: list[str] | None = None,
    models: ModelFactory | None = None,
) -> Played:
    clock, ids = FakeClock(START), FakeIdGenerator()
    config = scenario.config
    policy = PolicyConfig()
    if "policy.llm.max_calls_per_case" in config:
        policy = policy.model_copy(
            update={
                "llm": LlmConfig.model_validate(
                    {
                        **policy.llm.model_dump(),
                        "max_calls_per_case": int(config["policy.llm.max_calls_per_case"]),
                    }
                )
            }
        )
    modules_off = tuple(
        key.split(".")[1]
        for key, value in config.items()
        if key.startswith("modules.") and key.endswith(".enabled") and value is False
    )
    faults = scenario.faults
    reasoning_on = "reasoning" not in modules_off
    model = gateway = secondary = None
    reasoner = interpreter = None
    if reasoning_on:
        if models is None:
            raise ValueError("a scenario with reasoning on needs the composition root's models")
        model = ScriptedModel(
            list(actions if actions is not None else scenario.actions or []),
            scenario.fixture,
            faults.get("model_gateway.primary"),
        )
        built = models(model, policy, clock, ids)
        gateway, secondary = built.gateway, built.secondary
        reasoner, interpreter = built.reasoner, built.interpreter

    switch = Switch()
    world = open_world(
        str(scenario.fixture),
        policy=policy,
        clock=clock,
        ids=ids,
        reasoner=reasoner,
        interpreter=interpreter,
        decide=lambda _a: None,  # every decision is a scripted event, never automatic
        ledger_failure=faults.get("ledger"),
        modules_off=modules_off,
        evidence_faults={EVIDENCE_KEYS[k]: v for k, v in faults.items() if k in EVIDENCE_KEYS},
        kill_switch=switch,
        # S46: the status is what cannot be known at intake.
        status_known="evidence.shipment_status" not in faults,
    )
    played = Played(world, model, gateway, secondary)
    if "email.send" in faults:
        world.channel.faults["send"] = EMAIL_FAULTS[faults["email.send"]]
    if "email.create" in faults:
        world.channel.faults["send"] = EMAIL_FAULTS[faults["email.create"]]
    if "ticketing.create" in faults:
        world.tickets.faults["upsert_issue"] = "permanent"

    for step in scenario.steps:
        _at(world, step)
        _apply(played, step, switch)
        _settle(world)
        if world.channel.faults and world.channel.sent_count() + _drafts(world) >= 1:
            world.channel.faults.clear()  # a fault is a one-off in the catalogue's scripts
    return played


# -- steps -------------------------------------------------------------------------------------


def _at(world: World, step: Step) -> None:
    target = START + step.at
    if world.clock.now() < target:
        world.clock.set(target) if hasattr(world.clock, "set") else world.clock.advance(
            target - world.clock.now()
        )
    world.tick()


def _settle(world: World) -> None:
    """Let the case run to rest: automatic states advance, verification waits out the fakes."""
    world.drive(max_rounds=20)


def _apply(played: Played, step: Step, switch: Switch) -> None:
    world, name, args = played.world, step.name, step.args()
    if name == "dispute_raised":
        _dispute(played, step)
    elif name in ("approve", "reject"):
        _decide(played, step, args.get("", ""), name == "approve", args.get("note", ""))
    elif name == "seller_reply":
        label = args.get("", "any")
        world.reply(f"[reply:{label}] {bank.REPLIES.get(label, bank.REPLIES['any'])}")
    elif name == "ops_finding":
        _finding(world, args.get("", "no_fault_found"))
    elif name == "timer":
        _timer(world, TimerKind(args.get("", "")))
    elif name == "kill_switch":
        switch.on = args.get("", "on") == "on"
    elif name == "dispute_withdrawn":
        case = world.report().case
        _send(
            world,
            InboundEventType.DISPUTE_WITHDRAWN,
            "seller_portal",
            step,
            DisputeWithdrawn(
                shipment_ref=case.facts.shipment_ref,
                claim_ref=case.facts.claim_ref,
                seller_ref=case.facts.seller_ref,
                withdrawn_at=world.clock.now(),
                authenticated_principal=case.facts.seller_ref,
            ),
            actor=Actor(kind=ActorKind.EXTERNAL, id="seller_portal"),
        )
    elif name == "human_confirmed_withdrawal":
        _send(
            world,
            InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL,
            "ops_console",
            step,
            HumanConfirmedWithdrawal(note="confirmed with the seller"),
        )
    elif name == "human_took_case":
        _send(
            world,
            InboundEventType.HUMAN_TOOK_CASE,
            "ops_console",
            step,
            HumanTookCase(notify_seller=args.get("notify_seller", "false") == "true"),
        )
    elif name == "human_returned_case":
        _send(
            world,
            InboundEventType.HUMAN_RETURNED_CASE,
            "ops_console",
            step,
            HumanReturnedCase(
                reason="checked",
                unknown_action_resolutions=tuple(
                    UnknownActionResolution(
                        action_id=a.id,
                        resolution=args.get("resolution", "confirmed_not_done"),
                        evidence_note="checked by the reviewer",
                    )
                    for a in _actions(world)
                    if a.status is ActionStatus.UNKNOWN
                ),
            ),
        )
    elif name == "human_reopened_case":
        _world_changes(world, args.get("reason", ""))
        _send(
            world,
            InboundEventType.HUMAN_REOPENED_CASE,
            "ops_console",
            step,
            HumanReopenedCase(reason=args.get("reason", "reopened") or "reopened"),
        )
    elif name == "bounce_received":
        sent = [
            a
            for a in _actions(world)
            if a.action_type is ActionType.SEND_MESSAGE and a.external_ref
        ]
        _send(
            world,
            InboundEventType.DELIVERY_FAILURE_RECEIVED,
            "outlook",
            step,
            DeliveryFailureReceived(
                failed_external_message_id=str(sent[-1].external_ref), diagnostic_code="5.1.1"
            ),
            actor=Actor(kind=ActorKind.EXTERNAL, id="postmaster"),
        )
    else:
        raise NotImplementedError(f"script step {name!r}")


#: What a reopen reason says changed in the world, for the evidence the next round reads.
#: "delivery scan arrived late" (S43c): the shipment now shows as delivered.
WORLD_CHANGES = {"delivery scan arrived late": "DELIVERED"}


def _world_changes(world: World, reason: str) -> None:
    import dataclasses

    status = WORLD_CHANGES.get(reason)
    if not status or world.source is None or world.source.fixture is None:
        return
    built = world.source.fixture
    world.source.fixture = dataclasses.replace(
        built, shipment=dataclasses.replace(built.shipment, status=status)
    )


def _dispute(played: Played, step: Step) -> None:
    if not step.argument:
        return  # the world opened the case at the start
    if step.argument == "same id":
        with SqliteUnitOfWork(played.world.engine, clock=played.world.clock) as work:
            row = work.connection.exec_driver_sql(
                "SELECT event_json FROM inbox WHERE event_type = 'DISPUTE_RAISED' LIMIT 1"
            ).first()
            assert row is not None
            try:
                work.inbox.accept(parse_inbound(str(row[0])), played.world.clock.now())
            except DuplicateEvent:
                played.notes.append("duplicate dispute absorbed by dedupe")
        return
    if step.argument.startswith("new id"):
        # A new dispute on the same shipment and claim: intake decides - an open case
        # absorbs it, a recently closed soft-terminal case is reopened, otherwise a new case
        # opens linked to the old one (orchestrator.md section 2). The run follows that case.
        from dispute_agent.api.full_run import _open_case

        world = played.world
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            old = work.cases.get(world.case_id)
            facts = old.facts.model_copy(update={"raised_at": world.clock.now()})
            case, causation = _open_case(
                work, facts, f"{world.name}-again", world.clock.now(), world.line.ids
            )
        world.case_id, world.causation = case.id, causation
        world.path = [case.state]
        return
    raise NotImplementedError(f"dispute_raised({step.argument})")


def _decide(played: Played, step: Step, kind: str, yes: bool, note: str) -> None:
    world = played.world
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        pending = [
            a
            for a in work.approvals.for_case(world.case_id)
            if (a.request.subject is ApprovalSubject.CREDIT and kind == "credit")
            or (a.request.message_kind is not None and a.request.message_kind.value == kind)
        ]
    if not pending:
        played.notes.append(f"no approval for {kind} at {step.at}")
        return
    approval = pending[-1]
    sid = f"{approval.id}:decision"
    event = InboundEvent(
        type=InboundEventType.APPROVAL_DECIDED,
        source="approvals",
        source_event_id=sid,
        occurred_at=world.clock.now(),
        actor=OWNER,
        case_id=world.case_id,
        payload=ApprovalDecided(
            approval_id=approval.id,
            decision="APPROVED" if yes else "REJECTED",
            payload_hash_seen=approval.request.payload_hash,
            note=note,
        ),
    )
    world.runner.submit(event)


def _finding(world: World, label: str) -> None:
    import dataclasses

    assert world.source is not None and world.source.fixture is not None
    world.source.fixture = dataclasses.replace(world.source.fixture, finding=bank.FINDINGS[label])
    world.source.finding_available = True
    issues = [
        a for a in _actions(world) if a.action_type is ActionType.UPSERT_ISSUE and a.external_ref
    ]
    if not issues:
        return
    key = str(issues[-1].external_ref)
    world.runner.submit(
        InboundEvent(
            type=InboundEventType.OPS_FINDING_RECEIVED,
            source="jira",
            source_event_id=f"{key}:{label}:{world.clock.now().timestamp():.0f}",
            occurred_at=world.clock.now(),
            actor=Actor(kind=ActorKind.EXTERNAL, id="jira"),
            payload=OpsFindingReceived(
                finding=OpsFinding(
                    issue_key=key,
                    finding_id=label,
                    kind=FINDING_KIND[label],
                    note=(
                        UntrustedText.create(bank.FINDINGS[label].note or "", "jira_comment", key)
                        if bank.FINDINGS[label].note
                        else None
                    ),
                    at=world.clock.now(),
                )
            ),
        )
    )
    world._sync_path()


def _timer(world: World, kind: TimerKind) -> None:
    """Move the clock until the named timer is due (the catalogue's times are approximate)."""
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        armed = [t for t in work.timers.active_for_case(world.case_id) if t.kind is kind]
    if armed and armed[0].due_at > world.clock.now():
        world.clock.advance(armed[0].due_at - world.clock.now())
    world.tick()


def _send(
    world: World,
    kind: InboundEventType,
    source: str,
    step: Step,
    payload: Any,
    actor: Actor = OWNER,
) -> None:
    world.runner.submit(
        InboundEvent(
            type=kind,
            source=source,
            source_event_id=f"{kind.value}:{step.at}:{step.argument}",
            occurred_at=world.clock.now(),
            actor=actor,
            case_id=world.case_id,
            payload=payload,
        )
    )
    world._sync_path()


def _actions(world: World) -> list[Any]:
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        return list(work.actions.for_case(world.case_id))


def _drafts(world: World) -> int:
    return sum(1 for m in world.channel._all if m.folder == "drafts")


# -- the outcome -------------------------------------------------------------------------------


def _outcome(played: Played) -> Outcome:
    world = played.world
    world._sync_path()
    report = world.report()
    with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
        trail = list(work.events.list_for_case(world.case_id))
        sql = work.connection.exec_driver_sql
        cases = int(sql("SELECT count(*) FROM cases").scalar_one())
        credit_intents = int(
            sql("SELECT count(*) FROM action_intents WHERE action_type='POST_CREDIT'").scalar_one()
        )
        decided = int(
            sql(
                "SELECT count(*) FROM approvals WHERE decision IN ('APPROVED','REJECTED')"
            ).scalar_one()
        )
    entered = [
        e.payload.review_reason.render()  # type: ignore[attr-defined]
        for e in trail
        if e.type is InternalEventType.STATE_CHANGED
        and e.payload.to_state is CaseState.NEEDS_HUMAN_REVIEW  # type: ignore[attr-defined]
        and e.payload.review_reason is not None  # type: ignore[attr-defined]
    ]
    case = report.case
    return Outcome(
        path=[s.value for s in world.path],
        final=case.state.value,
        review=case.review_reason.render() if case.review_reason else None,
        first_review=entered[0] if entered else None,
        actions=[p.action.value for p in report.proposals],
        counts={
            "emails_sent": report.emails_sent,
            "ledger_credits": report.ledger_credits,
            "jira_issues": report.jira_issues,
            "approvals": len(report.approvals),
            "cases": cases,
            "credit_intents": credit_intents,
            "approval_decisions": decided,
            "input_events_dispute": sum(
                1 for e in trail if e.type is InboundEventType.DISPUTE_RAISED
            ),
            "model_route_failovers": len(played.gateway.failovers) if played.gateway else 0,
            "secondary_model_calls": len(played.secondary.requests) if played.secondary else 0,
        },
    )


__all__ = ["play"]
