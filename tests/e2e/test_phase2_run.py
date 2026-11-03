"""Card P2-09: a synthetic dispute gathers evidence and gets its charge recalculated.

Phase 1 reached a person with an honest "there is no pack yet". This is the first run where
the system actually works something out: the kurta dispute walks RECEIVED ->
GATHERING_EVIDENCE -> CALCULATING -> INVESTIGATING, and by the time it stops there is a
stored assessment with the charge recomputed from the measurements the sources returned.

It still ends with a person, because there is no reasoner until Phase 4. The difference is
what the person is handed: in Phase 1, nothing; here, a round of evidence and a number.

The three scenarios after the happy path are the ones that matter more:

- **S05b / S21** — the evidence module is off. The case reaches a person with
  `module_unavailable:evidence`, and it gets there through `assess`'s kernel rule rather
  than through anything the pack or a reasoner decided (I6).
- **S32** — intake could not tell whether the parcel was delivered; the evidence round
  could, and the assessment proves it ineligible (T05).
- **S46** — nothing can say what happened to the parcel at all, so eligibility escalates
  rather than guessing either way.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

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
    CaseState,
    EvidenceStatus,
    InboundEventType,
    ReviewCode,
    TimerKind,
)
from dispute_agent.kernel.domain.errors import NotFound
from dispute_agent.kernel.domain.events import Event, EventPayload, InboundEvent, InboundPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.orchestrator.intake import Intake
from dispute_agent.kernel.orchestrator.orchestrator import (
    EscalatingStep,
    Orchestrator,
    Step,
    phase_1_steps,
)
from dispute_agent.kernel.orchestrator.steps import (
    AssessStep,
    EligibilityStep,
    GatherEvidenceStep,
)
from dispute_agent.kernel.statemachine.machine import StateMachine
from dispute_agent.modules.cross_case.null import NullOutcomeObserver, NullPatternContextProvider
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
INPUT = EventId("evt_input0000000000000000000")
TABLE = default_table()
PACK = WeightDisputePack()


@pytest.fixture
def engine(tmp_path: Path):
    eng = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(eng, schema_sql_path().read_text(encoding="utf-8"))
    return eng


def facts_for(name: str, *, status: str | None = None) -> CaseFacts:
    built = fixture(name)
    pack_facts = {} if status == "" else {STATUS_KEY: status or built.shipment.status}
    return CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=NOW,
        pack_facts=pack_facts,
    )


def an_inbound(seller: str) -> InboundEvent:
    return InboundEvent(
        type=InboundEventType.DISPUTE_RAISED,
        source="dispute_api",
        source_event_id=f"form-{seller}",
        occurred_at=NOW,
        actor=Actor(kind=ActorKind.EXTERNAL, id="dispute_api"),
        payload=InboundPayload(),
    )


def run(engine, name: str, *, faults: dict[str, str] | None = None, status: str | None = None):
    """Intake a dispute from a fixture, then advance it as far as it will go."""
    facts = facts_for(name, status=status)
    clock = FakeClock(NOW)
    source = SyntheticShipmentSource(fixture=fixture(name), faults=faults or {})
    intake = Intake(allowed_recipients=(facts.seller_contact,), reopen_window=timedelta(days=7))

    with SqliteUnitOfWork(engine, clock=clock) as work:
        SqliteInboxStore(work.connection).accept(an_inbound(facts.seller_contact), NOW)
        opened = intake.open_or_get_case(
            work.cases,
            an_inbound(facts.seller_contact),
            facts,
            pack="weight_dispute",
            now=NOW,
            case_id=CaseId("case_01HQ"),
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
                dedupe_key=an_inbound(facts.seller_contact).dedupe_key,
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
                sources={"evidence": source},
                store=work.evidence,
                ids=FakeIdGenerator(),
                timers_store=work.timers,
            ),
            "assess": AssessStep(PACK, work.evidence, work.assessments),
            # Still escalating: there is no reasoner until Phase 4. What differs from
            # Phase 1 is that the person now receives a round of evidence and a number.
            "investigate": EscalatingStep(
                "investigate",
                ReviewCode.PROPOSAL_ESCALATED,
                "no reasoner until Phase 4",
            ),
        }
        advanced = Orchestrator(
            StateMachine(TABLE),
            steps,
            clock,
            FakeIdGenerator(),
            patterns=NullPatternContextProvider(),
            outcomes=NullOutcomeObserver(),
        ).advance(opened.case, raised.id)

        for event in advanced.events:
            work.events.append(event)
        work.cases.save(advanced.case, opened.case.version)

        assessment = work.assessments.latest(advanced.case.id)
        try:
            # None when the round rested: a resting round writes no evidence, and `bundle`
            # says so by raising rather than by handing back an empty one.
            bundle = work.evidence.bundle(advanced.case.id, advanced.case.gather_round)
        except NotFound:
            bundle = None
    return advanced, assessment, bundle


class TestTheKurtaRuns:
    def test_it_walks_the_documented_path(self, engine) -> None:
        advanced, _, _ = run(engine, "kurta_box_larger")
        states = [
            event.payload.to_state
            for event in advanced.events
            if getattr(event.payload, "to_state", None) is not None
        ]
        assert states[:3] == [
            CaseState.GATHERING_EVIDENCE,
            CaseState.CALCULATING,
            CaseState.INVESTIGATING,
        ]

    def test_the_evidence_round_was_written_down(self, engine) -> None:
        _, _, bundle = run(engine, "kurta_box_larger")
        assert bundle is not None
        keys = {item.result.requirement_key for item in bundle.items}
        assert {"shipment.measured_dims", "ledger.entries"} <= keys

    def test_the_charge_was_recalculated(self, engine) -> None:
        """The number a reader can check by hand: 1,980 g chargeable, 4 slabs, zone B."""
        _, assessment, _ = run(engine, "kurta_box_larger")
        assert assessment is not None
        assert assessment.recalculated == Money.of("152.00")
        assert assessment.charged == Money.of("152.00")

    def test_a_correct_bill_proves_no_error(self, engine) -> None:
        _, assessment, _ = run(engine, "kurta_box_larger")
        assert assessment is not None
        assert assessment.definite_errors == ()

    def test_the_assessment_belongs_to_the_evidence_it_claims(self, engine) -> None:
        """`input_hash` is the hash of the bundle it was computed from (I9)."""
        _, assessment, bundle = run(engine, "kurta_box_larger")
        from dispute_agent.kernel.domain.hashing import payload_hash

        assert assessment is not None and bundle is not None
        assert assessment.input_hash == payload_hash(bundle.model_dump(mode="json"))

    def test_an_overcharge_is_found_and_priced(self, engine) -> None:
        _, assessment, _ = run(engine, "calc_error_wrong_slab")
        assert assessment is not None
        assert assessment.definite_errors == ("calculation_error",)
        assert assessment.discrepancy == Money.of("38.00")


class TestTheEvidenceModuleIsOff:
    """S05b and S21: I6 arrives through the kernel rule in `assess`."""

    def test_the_case_reaches_a_person_naming_the_module(self, engine) -> None:
        advanced, _, _ = run(
            engine, "kurta_box_larger", faults={"shipment.measured_dims": "unavailable"}
        )
        assert advanced.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert advanced.case.review_reason is not None
        assert advanced.case.review_reason.code is ReviewCode.MODULE_UNAVAILABLE
        assert advanced.case.review_reason.module == "evidence"

    def test_the_round_that_failed_is_still_on_the_record(self, engine) -> None:
        """The point of recording a switched-off module as a FAILED item: the person sees
        what was asked for and what came back, not an empty case."""
        _, _, bundle = run(
            engine, "kurta_box_larger", faults={"shipment.measured_dims": "unavailable"}
        )
        assert bundle is not None
        failed = [i for i in bundle.items if i.result.status is EvidenceStatus.FAILED]
        assert [i.result.error_code for i in failed] == ["module_unavailable:evidence"]
        assert any(i.result.status is EvidenceStatus.FOUND for i in bundle.items)

    def test_nothing_was_assessed(self, engine) -> None:
        """`assess` escalates *before* asking the pack anything, so there is no assessment
        computed from a round that is missing its measurements."""
        _, assessment, _ = run(
            engine, "kurta_box_larger", faults={"shipment.measured_dims": "unavailable"}
        )
        assert assessment is None

    def test_an_optional_item_failing_does_not_stop_the_case(self, engine) -> None:
        """The required/optional boundary, end to end.

        Injected per key, which is a fixture's privilege rather than a real state - a
        genuinely disabled module fails every key, and then the required items fail too and
        the case escalates anyway. `tests/unit/orchestrator/test_steps.py` tests the
        boundary where it is unambiguous; this shows the wiring carries it.
        """
        advanced, assessment, _ = run(
            engine, "kurta_box_larger", faults={"shipment.image_description": "unavailable"}
        )
        assert advanced.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert advanced.case.review_reason is not None
        assert advanced.case.review_reason.code is ReviewCode.PROPOSAL_ESCALATED
        assert assessment is not None


class TestATransientFailureWaitsRatherThanDisappearing:
    """D41, end to end, against a real timer table.

    The unit tests check that the step arms a timer. This checks the row lands in the
    database in the same transaction as everything else, because the failure being guarded
    is a case resting with nothing scheduled to wake it.
    """

    def test_the_round_rests_and_an_evidence_retry_is_armed(self, engine) -> None:
        advanced, assessment, _ = run(
            engine, "kurta_box_larger", faults={"shipment.measured_dims": "timeout"}
        )
        assert advanced.case.state is CaseState.GATHERING_EVIDENCE
        assert assessment is None

        with SqliteUnitOfWork(engine, clock=FakeClock(NOW)) as work:
            armed = work.timers.active_for_case(advanced.case.id)
        assert [t.kind for t in armed] == [TimerKind.EVIDENCE_RETRY]
        assert armed[0].due_at == NOW + timedelta(minutes=15)
        assert armed[0].gather_round == advanced.case.gather_round


class TestIneligibilityProvenByEvidence:
    """S32: intake could not tell, the evidence round could (T05)."""

    def test_the_case_is_rejected_after_calculating(self, engine) -> None:
        advanced, _, _ = run(engine, "not_delivered_late_scan")
        assert advanced.case.state is CaseState.REJECTED_INELIGIBLE

    def test_the_assessment_is_what_proved_it(self, engine) -> None:
        _, assessment, _ = run(engine, "not_delivered_late_scan")
        assert assessment is not None
        assert assessment.eligible == "ineligible"
        assert assessment.reason_codes == ("not_delivered",)


class TestEligibilityCannotBeDetermined:
    """S46: the escalate branch of the eligibility step, and the only scenario for it."""

    def test_an_unknowable_status_escalates_rather_than_guessing(self, engine) -> None:
        advanced, _, _ = run(engine, "kurta_box_larger", status="")
        assert advanced.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert advanced.case.review_reason is not None
        assert advanced.case.review_reason.code is ReviewCode.ELIGIBILITY_UNKNOWN

    def test_it_never_reached_the_evidence_round(self, engine) -> None:
        """Eligibility decides whether the case may proceed at all, so there is nothing to
        gather for a case that may not."""
        advanced, assessment, _ = run(engine, "kurta_box_larger", status="")
        assert advanced.case.gather_round == 0
        assert assessment is None


class TestNotDeliveredAtIntake:
    """S17: refused on the shipment status before any evidence is gathered (T02)."""

    def test_an_rto_shipment_is_rejected_at_once(self, engine) -> None:
        advanced, _, _ = run(engine, "not_delivered")
        assert advanced.case.state is CaseState.REJECTED_INELIGIBLE
        assert advanced.case.gather_round == 0
