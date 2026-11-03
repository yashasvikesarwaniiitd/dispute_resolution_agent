"""Card P2-09: the three real steps, tested directly rather than only end to end.

This file exists because of a bug it would have caught. `GatherEvidenceStep` computed a
retry time, returned a rest, and nothing ever wrote the timer — so a required item that
timed out left the case sitting in GATHERING_EVIDENCE with nothing scheduled to wake it.
The round was never recorded, `assess` never ran, and D41's documented ending ("after the
last retry the round is recorded with the FAILED item") was unreachable. The end-to-end test
only ever injected a fault that does not rest, so the whole branch was invisible.

`TestRestingArmsTheRetry` is that test. The rule it encodes: **a step that rests must arm
the thing that brings it back, in the same transaction, or resting means disappearing.**

`TestAssessChecksI6First` is the other half. `assess` escalates on a required item that
failed because a module was off, *before* the pack is asked anything, so I6 never depends on
the pack or the reasoner noticing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import pytest

from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    CaseState,
    EvidenceStatus,
    Provenance,
    ReviewCode,
    TimerKind,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ConfigError, NotFound
from dispute_agent.kernel.domain.evidence import (
    ChargeAssessment,
    EligibilityResult,
    EvidenceBundle,
    EvidenceItem,
    EvidenceRequirement,
    EvidenceResult,
)
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import (
    AssessmentId,
    CaseId,
    CorrelationId,
    EvidenceItemId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Case
from dispute_agent.kernel.orchestrator.evidence import module_unavailable_code
from dispute_agent.kernel.orchestrator.steps import (
    AssessStep,
    EligibilityStep,
    GatherEvidenceStep,
)
from dispute_agent.kernel.policy.config import TimersConfig

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import Timer

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
CASE = CaseId("case_steps")
HASH = PayloadHash("sha256:" + "a" * 64)

DIMS = EvidenceRequirement(key="shipment.measured_dims", source="evidence", required=True)
PHOTO = EvidenceRequirement(key="shipment.image_description", source="evidence", required=False)

FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact="seller+1@example.invalid",
    raised_at=NOW,
)


def a_case(state: CaseState = CaseState.GATHERING_EVIDENCE, *, gather_round: int = 1) -> Case:
    return Case(
        id=CASE,
        pack="weight_dispute",
        facts=FACTS,
        state=state,
        version=3,
        correlation_id=CorrelationId("corr_steps"),
        opened_at=NOW,
        updated_at=NOW,
        state_entered_at=NOW,
        gather_round=gather_round,
    )


# -- fakes -----------------------------------------------------------------------------------


@dataclass
class FakeStore:
    items: list[EvidenceItem] = field(default_factory=list)

    def add(self, item: EvidenceItem) -> None:
        self.items.append(item)

    def bundle(self, case_id: CaseId, gather_round: int) -> EvidenceBundle:
        rows = [i for i in self.items if i.gather_round == gather_round]
        if not rows:
            raise NotFound(f"no evidence for {case_id} round {gather_round}")
        return EvidenceBundle(case_id=case_id, gather_round=gather_round, items=tuple(rows))


@dataclass
class FakeAssessments:
    saved: list[ChargeAssessment] = field(default_factory=list)

    def add(self, assessment: ChargeAssessment) -> None:
        self.saved.append(assessment)

    def latest(self, case_id: CaseId) -> ChargeAssessment | None:
        return self.saved[-1] if self.saved else None

    def get(self, assessment_id: object) -> ChargeAssessment | None:
        return next((a for a in self.saved if a.id == assessment_id), None)


@dataclass
class FakeTimers:
    fired: int = 0
    armed: list[Timer] = field(default_factory=list)

    def retries_this_round(self, case_id: CaseId, gather_round: int) -> int:
        return self.fired

    def schedule(self, timer: Timer) -> Timer:
        self.armed.append(timer)
        return timer


@dataclass
class FakeSource:
    code: str | None = None
    name: str = "evidence"

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        if self.code is None:
            return EvidenceResult(
                requirement_key=requirement.key,
                source=self.name,
                status=EvidenceStatus.FOUND,
                value={"ok": True},
                retrieved_at=NOW,
            )
        return EvidenceResult(
            requirement_key=requirement.key,
            source=self.name,
            status=EvidenceStatus.FAILED,
            error_code=self.code,
            retrieved_at=NOW,
        )


@dataclass
class FakePack:
    requirements: tuple[EvidenceRequirement, ...] = (DIMS,)
    outcome: Literal["eligible", "ineligible", "unknown"] = "eligible"
    eligible: Literal["eligible", "ineligible", "unknown"] = "eligible"

    def eligibility(self, facts: CaseFacts) -> EligibilityResult:
        codes = () if self.outcome == "eligible" else ("a_reason",)
        return EligibilityResult(outcome=self.outcome, reason_codes=codes)

    def evidence_plan(self, facts: CaseFacts) -> tuple[EvidenceRequirement, ...]:
        return self.requirements

    def assess(
        self,
        case_id: CaseId,
        gather_round: int,
        bundle: EvidenceBundle,
        *,
        claim_ref: str | None = None,
    ) -> ChargeAssessment:
        self.seen_claim_ref = claim_ref
        codes = () if self.eligible == "eligible" else ("not_delivered",)
        return ChargeAssessment(
            id=AssessmentId("asm_1"),
            case_id=case_id,
            gather_round=gather_round,
            pack_version="1.0.0",
            eligible=self.eligible,
            reason_codes=codes,
            input_hash=HASH,
        )


def gatherer(
    code: str | None = None,
    *,
    timers: FakeTimers | None = None,
    store: FakeStore | None = None,
    pack: FakePack | None = None,
) -> tuple[GatherEvidenceStep, FakeStore, FakeTimers]:
    the_store = store or FakeStore()
    the_timers = timers if timers is not None else FakeTimers()
    step = GatherEvidenceStep(
        pack=pack or FakePack(),
        sources={"evidence": FakeSource(code)},
        store=the_store,
        ids=FakeIdGenerator(),
        timers_store=the_timers,
        timers=TimersConfig(),
    )
    return step, the_store, the_timers


# -- eligibility -------------------------------------------------------------------------------


class TestEligibility:
    def test_eligible_passes(self) -> None:
        step = EligibilityStep(FakePack())
        assert step.run(a_case(CaseState.RECEIVED), NOW).trigger is Trigger.ELIGIBILITY_PASSED

    def test_ineligible_fails(self) -> None:
        step = EligibilityStep(FakePack(outcome="ineligible"))
        assert step.run(a_case(CaseState.RECEIVED), NOW).trigger is Trigger.ELIGIBILITY_FAILED

    def test_unknown_goes_to_a_person_rather_than_guessing(self) -> None:
        """S46. Guessing either way is unsafe, and the two guesses are wrong differently."""
        step = EligibilityStep(FakePack(outcome="unknown"))
        outcome = step.run(a_case(CaseState.RECEIVED), NOW)
        assert outcome.trigger is Trigger.ESCALATE
        assert outcome.review_reason is not None
        assert outcome.review_reason.code is ReviewCode.ELIGIBILITY_UNKNOWN


# -- the gather round ---------------------------------------------------------------------------


class TestAGoodRound:
    def test_the_round_is_recorded_and_the_case_moves_on(self) -> None:
        step, store, timers = gatherer()
        assert step.run(a_case(), NOW).trigger is Trigger.EVIDENCE_RECORDED
        assert len(store.items) == 1
        assert timers.armed == []

    def test_items_carry_the_round_they_were_gathered_in(self) -> None:
        step, store, _ = gatherer()
        step.run(a_case(gather_round=2), NOW)
        assert store.items[0].gather_round == 2

    def test_each_item_carries_a_hash_of_what_it_holds(self) -> None:
        step, store, _ = gatherer()
        step.run(a_case(), NOW)
        item = store.items[0]
        assert item.content_hash == payload_hash(item.result.model_dump(mode="json"))


class TestRestingArmsTheRetry:
    """The bug this file was written for.

    A step that rests must arm the thing that brings it back. Without the timer the case
    waits on nothing: `retries_this_round` stays at zero, so even a re-advance rests again,
    and the round is never recorded at all.
    """

    def test_a_transient_failure_arms_an_evidence_retry(self) -> None:
        step, _, timers = gatherer("timeout")
        assert step.run(a_case(), NOW).rests
        assert len(timers.armed) == 1
        assert timers.armed[0].kind is TimerKind.EVIDENCE_RETRY

    def test_the_timer_is_due_when_the_gather_round_said(self) -> None:
        step, _, timers = gatherer("timeout")
        step.run(a_case(), NOW)
        assert timers.armed[0].due_at == NOW + timedelta(minutes=15)

    def test_the_timer_carries_the_round_so_the_limit_is_counted_per_round(self) -> None:
        """Finding B22. Without this field D41's three-per-round limit cannot be counted,
        and a case that had one bad afternoon never retries a genuine blip again."""
        step, _, timers = gatherer("timeout")
        step.run(a_case(gather_round=4), NOW)
        assert timers.armed[0].gather_round == 4

    def test_it_records_the_version_and_state_it_was_armed_in(self) -> None:
        """Pre-dispatch rule P1 uses both to tell a live timer from a stale one."""
        step, _, timers = gatherer("timeout")
        step.run(a_case(), NOW)
        assert timers.armed[0].armed_at_version == 3
        assert timers.armed[0].armed_in_state is CaseState.GATHERING_EVIDENCE

    def test_no_evidence_is_written_while_resting(self) -> None:
        """A half-recorded round would be assessed as a complete one."""
        step, store, _ = gatherer("timeout")
        step.run(a_case(), NOW)
        assert store.items == []

    def test_once_the_retries_are_spent_the_round_is_recorded_with_the_failure(self) -> None:
        """D41's documented ending, which the missing timer made unreachable."""
        step, store, timers = gatherer("timeout", timers=FakeTimers(fired=3))
        assert step.run(a_case(), NOW).trigger is Trigger.EVIDENCE_RECORDED
        assert timers.armed == []
        assert [i.result.error_code for i in store.items] == ["timeout"]

    def test_a_permanent_failure_arms_nothing_and_records_at_once(self) -> None:
        """S21: three retries over 45 minutes for something that will not succeed."""
        step, store, timers = gatherer("error_permanent")
        assert step.run(a_case(), NOW).trigger is Trigger.EVIDENCE_RECORDED
        assert timers.armed == []
        assert len(store.items) == 1

    def test_an_optional_failure_does_not_hold_the_case_up(self) -> None:
        step, _, timers = gatherer("timeout", pack=FakePack(requirements=(PHOTO,)))
        assert not step.run(a_case(), NOW).rests
        assert timers.armed == []

    def test_resting_with_nowhere_to_write_the_timer_is_refused(self) -> None:
        """Failing loudly beats resting into silence. A step with no timer store that
        rests anyway is the bug this file exists for, in its purest form."""
        step = GatherEvidenceStep(
            pack=FakePack(),
            sources={"evidence": FakeSource("timeout")},
            store=FakeStore(),
            ids=FakeIdGenerator(),
            timers_store=None,
        )
        with pytest.raises(ConfigError, match="EVIDENCE_RETRY"):
            step.run(a_case(), NOW)


# -- assess --------------------------------------------------------------------------------------


def assessed(code: str | None, *, required: bool = True, pack: FakePack | None = None):
    """Gather a round, then assess it.

    The retries are spent (`fired=3`) so that a transient failure records the round rather
    than resting - otherwise there would be no bundle to assess, which is the gather step's
    behaviour and not what these tests are about.
    """
    requirement = DIMS if required else PHOTO
    the_pack = pack or FakePack(requirements=(requirement,))
    step, store, _ = gatherer(code, pack=the_pack, timers=FakeTimers(fired=3))
    step.run(a_case(), NOW)
    saved = FakeAssessments()
    return AssessStep(the_pack, store, saved).run(a_case(CaseState.CALCULATING), NOW), saved


class TestAssessChecksI6First:
    def test_a_required_item_on_a_dead_module_escalates_with_its_name(self) -> None:
        outcome, _ = assessed(module_unavailable_code("evidence"))
        assert outcome.trigger is Trigger.ESCALATE
        assert outcome.review_reason is not None
        assert outcome.review_reason.code is ReviewCode.MODULE_UNAVAILABLE
        assert outcome.review_reason.module == "evidence"

    def test_nothing_is_assessed_on_that_path(self) -> None:
        """Before the pack is asked anything: I6 does not get to depend on the pack, or on
        the reasoner after it, noticing that a module was off."""
        _, saved = assessed(module_unavailable_code("evidence"))
        assert saved.saved == []

    def test_an_optional_item_on_a_dead_module_does_not(self) -> None:
        """Otherwise every dispute reaches a person the day the image service is off."""
        outcome, saved = assessed(module_unavailable_code("evidence"), required=False)
        assert outcome.trigger is Trigger.ASSESSMENT_READY
        assert len(saved.saved) == 1

    def test_a_timeout_that_ran_out_of_retries_is_assessed_around(self) -> None:
        """It is not a switched-off module. The round is recorded with the gap in it, and
        the gap is what the baseline's B1 reads (D41's documented ending)."""
        outcome, saved = assessed("timeout")
        assert outcome.trigger is Trigger.ASSESSMENT_READY
        assert len(saved.saved) == 1


class TestAssessRecordsAndRoutes:
    def test_a_clean_round_is_assessed_and_moves_on(self) -> None:
        outcome, saved = assessed(None)
        assert outcome.trigger is Trigger.ASSESSMENT_READY
        assert len(saved.saved) == 1

    def test_it_tells_the_pack_which_charge_line_the_dispute_is_about(self) -> None:
        """Without it the pack takes the first DEBIT in the round, and a shipment with two
        charge lines recalculates one dispute against the other's charge (I9)."""
        pack = FakePack()
        step, store, _ = gatherer(None, pack=pack)
        step.run(a_case(), NOW)
        AssessStep(pack, store, FakeAssessments()).run(a_case(CaseState.CALCULATING), NOW)
        assert pack.seen_claim_ref == FACTS.claim_ref

    def test_proven_ineligibility_rejects_the_case(self) -> None:
        """S32: intake could not tell, the evidence round could (T05)."""
        pack = FakePack(eligible="ineligible")
        outcome, _ = assessed(None, pack=pack)
        assert outcome.trigger is Trigger.ASSESSMENT_INELIGIBLE

    def test_a_round_that_was_never_recorded_is_not_assessed_as_an_empty_one(self) -> None:
        """`bundle` raises rather than returning nothing. An empty bundle would read as
        every source having answered "there is nothing there"."""
        with pytest.raises(NotFound):
            AssessStep(FakePack(), FakeStore(), FakeAssessments()).run(
                a_case(CaseState.CALCULATING), NOW
            )


class TestProvenance:
    def test_a_gathered_item_is_marked_system(self) -> None:
        step, store, _ = gatherer()
        step.run(a_case(), NOW)
        assert store.items[0].provenance is Provenance.SYSTEM

    def test_an_item_id_is_minted_not_guessed(self) -> None:
        step, store, _ = gatherer(pack=FakePack(requirements=(DIMS, PHOTO)))
        step.run(a_case(), NOW)
        ids = [i.id for i in store.items]
        assert len(set(ids)) == len(ids)
        assert all(str(i).startswith("evi_") for i in ids)
        assert EvidenceItemId(str(ids[0])) == ids[0]
