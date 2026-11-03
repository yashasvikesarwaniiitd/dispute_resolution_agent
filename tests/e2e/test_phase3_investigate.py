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
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.investigate import InvestigateStep
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, Step, phase_1_steps
from dispute_agent.kernel.orchestrator.steps import AssessStep, EligibilityStep, GatherEvidenceStep
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.numbers import extract_numbers, justify
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


def facts_for(name: str) -> CaseFacts:
    built = fixture(name)
    return CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=NOW,
        pack_facts={STATUS_KEY: built.shipment.status},
    )


def run(engine, name: str):
    """Intake a fixture's dispute and advance it as far as it will go, with the real
    investigate station in place. Returns the case and what the station wrote."""
    facts = facts_for(name)
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
    config = PolicyConfig(
        messages=MessagesConfig(allowed_recipients=frozenset({facts.seller_contact}))
    )

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
                pack=PACK,  # type: ignore[arg-type]
                reasoner=RulesOnlyReasoner(pack=PACK, ids=ids, clock=clock),
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
        """I9, through the same check the policy engine ran. The regression this protects:
        the case id in the subject used to be flagged as an unexplained number."""
        case, _, intent, assessment = run(engine, "scanner_fault")
        assert intent is not None and assessment is not None
        facts = {f.fact_id: f for f in assessment.facts}
        masks = (str(case.id), case.facts.shipment_ref, case.facts.claim_ref, case.facts.seller_ref)
        for text in (intent.payload.subject, intent.payload.body):
            for token in extract_numbers(text, masks=masks):
                assert justify(token, intent.payload.numbers_used, facts) is None, token.text

    def test_the_weight_in_the_email_carries_its_unit(self, engine) -> None:
        """The other regression: it used to read "a size-based weight of 2,244" - grams or
        kilos, nobody could tell."""
        _, _, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        assert "2,244 g" in intent.payload.body

    def test_nothing_was_sent(self, engine) -> None:
        """The furthest this station goes is the approval queue."""
        _, approvals, _, _ = run(engine, "scanner_fault")
        assert approvals[0].used_by_action_id is None


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

    def test_a_seller_not_on_the_allow_list_is_refused_before_any_draft_is_filed(
        self, engine
    ) -> None:
        """I11, at planning. The scanner_fault run above reaches the queue only because the
        test config allow-lists this seller; this is the control - the same plan addressed to
        anyone else is refused by the rule the step actually runs."""
        from dispute_agent.kernel.policy.rules import RecipientAllowlisted
        from dispute_agent.kernel.policy.verdicts import PolicyContext

        case, _, intent, _ = run(engine, "scanner_fault")
        assert intent is not None
        stranger = intent.model_copy(
            update={
                "payload": intent.payload.model_copy(update={"to": "someone@elsewhere.invalid"})
            }
        )
        from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind

        verdict = RecipientAllowlisted().evaluate(
            PolicyContext(
                case=case,
                stage=PolicyStage.PLANNING,
                intent=stranger,
                policy=PolicyConfig(
                    messages=MessagesConfig(
                        allowed_recipients=frozenset({case.facts.seller_contact})
                    )
                ),
                now=NOW,
            )
        )
        assert verdict.verdict is VerdictKind.DENY


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
