"""The investigate station, end to end against a real database.

Until now every case that reached INVESTIGATING was handed to a person, because the station
was empty. These tests run real synthetic disputes through the whole line and check what
comes out, in the three outcomes that matter:

- **scanner_fault** — the box measured bigger than declared, no photo yet. The rules ask the
  seller for a photo: an email is drafted, written down as a plan, and filed for approval.
  Every number in it must pass the number check, and the approval must be bound to its text.
- **kurta_box_larger** — the round carries an image the rules cannot judge (B5). A person.
- **calc_error_wrong_slab** — a provable overcharge. A person too, because credits are Phase 5
  and the netted amount has no caller yet; guessing the amount is the failure to avoid.

Nothing here sends anything. The furthest the line goes is your approval queue.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.inbox_store import SqliteInboxStore
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    IntentStatus,
    MessageKind,
    ReviewCode,
)
from dispute_agent.kernel.domain.events import Event, EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, Step, phase_1_steps
from dispute_agent.kernel.orchestrator.steps import AssessStep, EligibilityStep, GatherEvidenceStep
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
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
    from collections.abc import Callable
    from pathlib import Path

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
INPUT = EventId("evt_input0000000000000000000")
CASE = CaseId("case_01HQ")
TABLE = default_table()
PACK = WeightDisputePack()
ALL_MODULES = dict.fromkeys(
    ("email", "ticketing", "ledger", "approvals", "evidence", "reasoning"), True
)


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


class RestrictedMenu:
    """The pack, with its menu replaced - to test what happens when the rules choose
    something the case may not do."""

    def __init__(self, menu: tuple[Any, ...]) -> None:
        self.menu = menu

    def allowed_actions(self, state: object, assessment: object) -> tuple[Any, ...]:
        return self.menu


def facts_for(name: str, statement: str | None = None) -> CaseFacts:
    built = fixture(name)
    return CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=NOW,
        statement=(
            UntrustedText.create(statement, "dispute_form", "form-1") if statement else None
        ),
        pack_facts={STATUS_KEY: built.shipment.status},
    )


def run(
    engine,
    name: str,
    *,
    menu: tuple[Any, ...] | None = None,
    allow_seller: bool = True,
    reasoner: Callable[[FakeIdGenerator, FakeClock], Any] | None = None,
    fallback: bool = False,
    statement: str | None = None,
):
    """Intake a fixture's dispute and advance it as far as it will go, with the real
    investigate station in place. Returns the case and what the station wrote.

    `reasoner` swaps the rules for another reasoner (Phase 4P: the model-backed one);
    `fallback` gives the station the rules baseline to fall back to.
    """
    facts = facts_for(name, statement)
    clock = FakeClock(NOW)
    ids = FakeIdGenerator()
    inbound = InboundEvent(
        type=InboundEventType.DISPUTE_RAISED,
        source="dispute_api",
        source_event_id=f"form-{name}",
        occurred_at=NOW,
        actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        payload=InboundPayload(),
    )
    # The recipient allow-list names this seller, as the owner's .env would name the test
    # seller. R06 refuses any other address (I11).
    allowed = (
        frozenset({facts.seller_contact})
        if allow_seller
        else frozenset({"someone.else@example.invalid"})
    )
    config = PolicyConfig(messages=MessagesConfig(allowed_recipients=allowed))

    with SqliteUnitOfWork(engine, clock=clock) as work:
        SqliteInboxStore(work.connection).accept(inbound, NOW)
        opened = Intake(
            allowed_recipients=(facts.seller_contact,), reopen_window=timedelta(days=7)
        ).open_or_get_case(
            work.cases,
            inbound,
            facts,
            pack="weight_dispute",
            now=NOW,
            case_id=CASE,
            correlation_id=CorrelationId("corr_01HQ"),
            opened_event_id=EventId("evt_opened000000000000000000"),
            causation_id=INPUT,
        )
        work.cases.insert(opened.case)
        raised = work.events.append(
            Event(
                id=INPUT,
                case_id=opened.case.id,
                type=InboundEventType.DISPUTE_RAISED,
                occurred_at=NOW,
                recorded_at=NOW,
                actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
                cause=opened.events[0].cause,
                correlation_id=opened.case.correlation_id,
                dedupe_key=inbound.dedupe_key,
                payload=EventPayload(),
            )
        )
        for event in opened.events:
            work.events.append(event)

        steps: dict[str, Step] = {
            **phase_1_steps(),
            "eligibility": EligibilityStep(PACK),
            "gather_evidence": GatherEvidenceStep(
                pack=PACK,
                sources={"evidence": SyntheticShipmentSource(fixture=fixture(name), clock=clock)},
                store=work.evidence,
                ids=ids,
                timers_store=work.timers,
            ),
            "assess": AssessStep(PACK, work.evidence, work.assessments),
            "investigate": InvestigateStep(
                pack=RestrictedMenu(menu) if menu is not None else PACK,  # type: ignore[arg-type]
                reasoner=(
                    reasoner(ids, clock)
                    if reasoner
                    else RulesOnlyReasoner(pack=PACK, ids=ids, clock=clock)
                ),
                fallback=(
                    RulesOnlyReasoner(pack=PACK, ids=ids, clock=clock, fallback_used=True)
                    if fallback
                    else None
                ),
                drafter=TemplateDrafter(),
                policy=PolicyEngine(phase_3_rules(), clock, ids),
                evidence=work.evidence,
                assessments=work.assessments,
                intents=work.intents,
                approvals=work.approvals,
                ids=ids,
                modules=ALL_MODULES,
                config=config,
            ),
        }
        advanced = Orchestrator(
            StateMachine(TABLE),
            steps,
            clock,
            ids,
            patterns=NullPatternContextProvider(),
            outcomes=NullOutcomeObserver(),
        ).advance(
            opened.case,
            raised.id,
            # Saved after every transition, as the real runtime does. Saving once at the end
            # is refused by the database as an illegal jump once a case moves more than one
            # state - which this test exists to exercise.
            commit=lambda before, after: work.cases.save(after, before.version),
        )
        for event in advanced.events:
            work.events.append(event)

        approvals = list(work.approvals.pending(CASE))
        intent = work.intents.get(approvals[0].request.intent_id) if approvals else None
        assessment = work.assessments.latest(CASE)
    return advanced.case, approvals, intent, assessment


class TestScannerFaultAsksTheSellerForAPhoto:
    """The first time the line produces something a person outside the system would read."""

    def test_the_case_waits_for_your_approval(self, engine) -> None:
        case, approvals, _, _ = run(engine, "scanner_fault")
        assert case.state is CaseState.AWAITING_APPROVAL
        assert len(approvals) == 1

    def test_the_plan_is_an_evidence_request_to_the_seller(self, engine) -> None:
        _, _, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        assert intent.payload.kind is MessageKind.EVIDENCE_REQUEST
        assert intent.payload.to == facts_for("scanner_fault").seller_contact
        assert intent.status is IntentStatus.PENDING_APPROVAL

    def test_the_approval_is_bound_to_the_plan_s_exact_text(self, engine) -> None:
        """I13. The yes you give is about this hash, and no other text can borrow it."""
        _, approvals, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        assert approvals[0].request.payload_hash == intent.payload_hash
        assert approvals[0].decision is ApprovalDecision.PENDING

    def test_the_email_asks_for_the_documented_items(self, engine) -> None:
        _, _, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        assert "Photo of the packed box beside a tape measure" in intent.payload.body

    def test_every_number_in_the_email_is_a_stored_fact(self, engine) -> None:
        """I9, asked of the rule the station runs. An earlier version rebuilt R09's mask
        list by hand and would have passed even if R09's own masking drifted (second
        review, finding 8)."""
        from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind
        from dispute_agent.kernel.policy.rules import MessageNumbersVerified
        from dispute_agent.kernel.policy.verdicts import PolicyContext

        case, _, intent, assessment = run(engine, "scanner_fault")
        assert intent is not None and assessment is not None
        verdict = MessageNumbersVerified().evaluate(
            PolicyContext(
                case=case,
                stage=PolicyStage.EXECUTION,
                intent=intent,
                assessment=assessment,
                now=NOW,
            )
        )
        assert verdict.verdict is VerdictKind.ALLOW, verdict.reason

    def test_the_weight_in_the_email_carries_its_unit(self, engine) -> None:
        """The other regression: it used to read "a size-based weight of 2,244" - grams or
        kilos, nobody could tell."""
        _, _, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        assert "2,244 g" in intent.payload.body


class TestCasesTheRulesCannotDecide:
    def test_an_image_goes_to_a_person(self, engine) -> None:
        """B5: the rules never judge a picture."""
        case, approvals, _, _ = run(engine, "kurta_box_larger")
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.PROPOSAL_ESCALATED
        assert approvals == []

    def test_a_provable_overcharge_goes_to_a_person_until_credits_exist(self, engine) -> None:
        """Credits are Phase 5. Taking the raw discrepancy as the amount would pay the wrong
        figure on a second dispute, so the honest answer today is a person."""
        case, approvals, _, _ = run(engine, "calc_error_wrong_slab")
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.POLICY_ESCALATED
        assert "Phase 5" in (case.review_reason.detail or "")
        assert approvals == []


class TestTheReviewerSeesTheEmail:
    """Phase 3 review, finding 3. The page used to show only the hash: a person approving a
    sha256 digest they could not read, with the code that built this view never called."""

    def view_of_the_pending_approval(self, engine: Any) -> tuple[Any, Any]:
        from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole, sign_in

        _, approvals, intent, _ = run(engine, "scanner_fault")
        with SqliteUnitOfWork(engine, clock=FakeClock(NOW)) as work:
            console = ReviewConsole(
                store=work.approvals,
                clock=FakeClock(NOW),
                reviewer=sign_in("t" * 20, "t" * 20, "owner"),
            )
            return console.view(
                approvals[0], intents=work.intents, assessments=work.assessments
            ), intent

    def test_it_shows_the_stored_subject_and_body_exactly(self, engine) -> None:
        """The text whose hash the approval binds to - not a re-rendering of it."""
        item, intent = self.view_of_the_pending_approval(engine)
        assert item.subject == intent.payload.subject
        assert item.body == intent.payload.body
        assert "Photo of the packed box beside a tape measure" in item.body

    def test_every_figure_in_the_email_is_shown_with_its_source(self, engine) -> None:
        """ "Approve this email" is hard to answer well. "Approve this email, in which
        INR 190.00 is the amount charged" is not."""
        item, _ = self.view_of_the_pending_approval(engine)
        shown = dict(item.numbers)
        assert shown["₹190.00"] == "amount charged"
        assert shown["2,244 g"] == "chargeable weight in grams"

    def test_only_the_figures_this_email_uses_are_listed(self, engine) -> None:
        """Every fact on the case would bury the two that matter."""
        item, intent = self.view_of_the_pending_approval(engine)
        assert len(item.numbers) == len(intent.payload.numbers_used)


class TestTheMenuIsEnforced:
    """Second Phase 3 review, finding 3. The menu was built, carried on the briefing and
    then ignored."""

    def test_an_off_menu_choice_goes_to_a_person_and_files_nothing(self, engine) -> None:
        from dispute_agent.kernel.domain.enums import ProposalAction

        # The rules choose "ask the seller for a photo"; this case may only escalate or wait.
        case, approvals, intent, _ = run(
            engine,
            "scanner_fault",
            menu=(ProposalAction.ESCALATE_TO_HUMAN, ProposalAction.WAIT),
        )
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.INVALID_MODEL_OUTPUT
        assert approvals == []
        assert intent is None

    def test_the_same_choice_on_the_menu_goes_through(self, engine) -> None:
        """The control."""
        case, approvals, _, _ = run(engine, "scanner_fault")
        assert case.state is CaseState.AWAITING_APPROVAL
        assert len(approvals) == 1


class TestAnAddressNotOnTheAllowListFilesNothing:
    """Replaces a test that called the rule directly and claimed more than it showed
    (second review, finding 8). This runs the whole station."""

    def test_nothing_reaches_the_approval_queue(self, engine) -> None:
        case, approvals, intent, _ = run(engine, "scanner_fault", allow_seller=False)
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.POLICY_DENIED
        assert "R06" in (case.review_reason.detail or "")
        assert approvals == []
        assert intent is None


class TestTheReviewPageGuards:
    """Second Phase 3 review, finding 7."""

    def test_the_recipient_is_shown(self, engine: Any) -> None:
        item, intent = TestTheReviewerSeesTheEmail().view_of_the_pending_approval(engine)
        assert item.to == intent.payload.to
        assert item.to == facts_for("scanner_fault").seller_contact

    def test_an_untampered_message_matches_its_approval(self, engine: Any) -> None:
        item, _ = TestTheReviewerSeesTheEmail().view_of_the_pending_approval(engine)
        assert item.matches_approval is True

    def test_a_message_edited_behind_the_system_is_flagged(self, engine: Any) -> None:
        """Someone edits the stored text directly. The approval is bound to the old text;
        the page must say so rather than show the new text as if it were approved."""
        import json

        from sqlalchemy import update

        from dispute_agent.infra.sqlite.tables import action_intents
        from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole, sign_in

        _, approvals, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        tampered = intent.payload.model_copy(update={"body": "Please pay us more."})
        with SqliteUnitOfWork(engine, clock=FakeClock(NOW)) as work:
            work.connection.execute(
                update(action_intents)
                .where(action_intents.c.id == str(intent.id))
                .values(payload_json=json.dumps(tampered.model_dump(mode="json")))
            )
        with SqliteUnitOfWork(engine, clock=FakeClock(NOW)) as work:
            console = ReviewConsole(
                store=work.approvals,
                clock=FakeClock(NOW),
                reviewer=sign_in("t" * 20, "t" * 20, "owner"),
            )
            item = console.view(approvals[0], intents=work.intents, assessments=work.assessments)
        assert item.matches_approval is False
