"""The Phase 2 steps: eligibility, gather_evidence and assess (P2-09, orchestrator.md 3).

Phase 1 had every step escalate with a reason naming the phase that would replace it. These
are the first three real ones. `investigate` still escalates: there is no reasoner until
Phase 4, and the baseline that will stand in for it is built but not yet wired, because
wiring it is a decision about what runs when the model is down rather than a step.

**The kernel rule in `assess` is the load-bearing part of this file.** Before the pack is
asked anything, `assess` checks whether a required evidence item failed because a module was
switched off, and escalates with that module's name if so. It is written here, in the
kernel, rather than left to the pack or the reasoner, because I6 must not depend on either
of them noticing. The gather round deliberately records a switched-off module as a FAILED
item instead of raising — that keeps the rest of the round — and this is the other half of
that bargain: the case still reaches a person with `module_unavailable:<name>`, one step
later and with the evidence written down.

The steps commit nothing themselves. Each writes through the stores it was given inside the
unit of work the loop opened, and returns the trigger; the loop applies the transition and
appends the events. Deciding and recording stay apart, which is what lets the whole table be
exercised without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.actions import Timer
from dispute_agent.kernel.domain.enums import (
    EvidenceStatus,
    Provenance,
    ReviewCode,
    TimerKind,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.evidence import EvidenceBundle, EvidenceItem
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import EvidenceItemId, TimerId
from dispute_agent.kernel.domain.models import ReviewReason
from dispute_agent.kernel.orchestrator.evidence import MODULE_UNAVAILABLE, gather
from dispute_agent.kernel.orchestrator.orchestrator import StepOutcome

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import (
        ChargeAssessment,
        EligibilityResult,
        EvidenceRequirement,
        EvidenceResult,
    )
    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.policy.config import TimersConfig
    from dispute_agent.kernel.ports import (
        AssessmentStore,
        EvidenceSource,
        EvidenceStore,
        IdGenerator,
    )


class ReplyRound(Protocol):
    def read(self, case: Case, now: datetime) -> int: ...


class RetryTimers(Protocol):
    """Counting the EVIDENCE_RETRY timers of a gather round, and arming the next one.

    Both halves, because the step needs both and splitting them is how the first version of
    this lost a case. It computed a due time, returned a rest, and nothing ever wrote the
    timer: the case sat in GATHERING_EVIDENCE with nothing scheduled to wake it, the round
    was never recorded, and D41's documented ending - "after the last retry the round is
    recorded with the FAILED item" - was unreachable. A step that rests has to arm the thing
    that brings it back, in the same transaction, or resting means disappearing.

    Still narrower than a TimerStore: it cannot cancel, and which timers a state cancels is
    the transition table's business, not a step's.
    """

    def retries_this_round(self, case_id: CaseId, gather_round: int) -> int: ...

    def schedule(self, timer: Timer) -> Timer: ...
    def active_for_case(self, case_id: CaseId) -> Sequence[Timer]: ...


class CasePack(Protocol):
    """The three things these steps ask a pack for.

    Narrower than `DomainPack` on purpose. A step typed against the whole port could reach
    `baseline_proposal` and `render_template`, and then "the gather step does not decide
    anything" would be a convention rather than something the type says. It also lets the
    steps be used before a pack implements the parts of the port Phase 3 fills in.
    """

    def eligibility(self, facts: CaseFacts) -> EligibilityResult: ...

    def evidence_plan(self, facts: CaseFacts) -> Sequence[EvidenceRequirement]: ...

    def assess(
        self,
        case_id: CaseId,
        gather_round: int,
        bundle: EvidenceBundle,
        *,
        claim_ref: str | None = None,
    ) -> ChargeAssessment: ...


@dataclass
class EligibilityStep:
    """May this dispute proceed at all? Three answers, and `unknown` goes to a person."""

    pack: CasePack
    #: Completes the facts eligibility needs from evidence when the dispute did not carry
    #: them (the delivery status). A source that is switched off raises ModuleUnavailable,
    #: which the loop turns into module_unavailable:<module> - never a guess (I6, S46).
    facts: Callable[[Case], CaseFacts] | None = None
    name: str = "eligibility"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        facts = self.facts(case) if self.facts is not None else case.facts
        result = self.pack.eligibility(facts)
        if result.outcome == "eligible":
            return StepOutcome(trigger=Trigger.ELIGIBILITY_PASSED)
        if result.outcome == "ineligible":
            return StepOutcome(
                trigger=Trigger.ELIGIBILITY_FAILED,
                detail=", ".join(result.reason_codes),
            )
        return StepOutcome(
            trigger=Trigger.ESCALATE,
            review_reason=ReviewReason(
                code=ReviewCode.ELIGIBILITY_UNKNOWN,
                detail=", ".join(result.reason_codes),
            ),
            detail="eligibility could not be determined",
        )


@dataclass
class GatherEvidenceStep:
    """One gather round, written down (P2-06).

    It rests rather than transitioning when a required item failed transiently and the
    round has retries left. Resting is not finishing: the EVIDENCE_RETRY timer the
    transition table arms brings the case back here.
    """

    pack: CasePack
    sources: Mapping[str, EvidenceSource]
    store: EvidenceStore
    ids: IdGenerator
    timers_store: RetryTimers | None = None
    timers: TimersConfig | None = None
    #: Reads the seller's replies into this round (the reply round). None: no replies can
    #: arrive in this assembly, as before the dispatcher existed.
    replies: ReplyRound | None = None
    name: str = "gather_evidence"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        if self.timers_store is not None and any(
            t.kind is TimerKind.EVIDENCE_RETRY for t in self.timers_store.active_for_case(case.id)
        ):
            # A retry is already armed for this round: rest until it fires. Fetching again
            # now would arm a second one (the runner advances resting cases on every tick).
            return StepOutcome(detail="waiting for the evidence retry")
        fired = (
            self.timers_store.retries_this_round(case.id, case.gather_round)
            if self.timers_store is not None
            else 0
        )
        outcome = gather(
            case_id=case.id,
            facts=case.facts,
            requirements=self.pack.evidence_plan(case.facts),
            sources=self.sources,
            now=now,
            retries_fired_this_round=fired,
            timers=self.timers,
        )

        if outcome.rests:
            # No evidence is written: the round is not over, and a half-recorded round would
            # be assessed as a complete one by whatever reaches `assess` first. The timer
            # *is* written, because it is the only thing that will bring the case back here.
            assert outcome.retry_due_at is not None
            self._arm_retry(case, outcome.retry_due_at, now)
            return StepOutcome(detail=outcome.rest_reason)

        for result in outcome.results:
            self.store.add(self._item(case, result))
        # Replies are read once the round is complete, never on a rest: a reply read into a
        # round that then retries would be read again into the next one.
        if self.replies is not None:
            self.replies.read(case, now)
        return StepOutcome(trigger=Trigger.EVIDENCE_RECORDED)

    def _arm_retry(self, case: Case, due_at: datetime, now: datetime) -> None:
        """Arm EVIDENCE_RETRY for this round, so resting is waiting rather than stopping."""
        if self.timers_store is None:
            raise ConfigError(
                "gather_evidence rested with no timer store to arm EVIDENCE_RETRY with; "
                "the case would wait for something nobody scheduled (D41)"
            )
        self.timers_store.schedule(
            Timer(
                id=TimerId(self.ids.new_id("tmr")),
                case_id=case.id,
                kind=TimerKind.EVIDENCE_RETRY,
                due_at=due_at,
                anchor_at=now,
                armed_in_state=case.state,
                armed_at_version=case.version,
                # EVIDENCE_RETRY alone carries this, and D41's limit cannot be counted per
                # round without it (finding B22).
                gather_round=case.gather_round,
                correlation_id=case.correlation_id,
                created_at=now,
            )
        )

    def _item(self, case: Case, result: EvidenceResult) -> EvidenceItem:
        return EvidenceItem(
            id=EvidenceItemId(self.ids.new_id("evi")),
            case_id=case.id,
            gather_round=case.gather_round,
            provenance=Provenance.SYSTEM,
            result=result,
            content_hash=payload_hash(result.model_dump(mode="json")),
        )


@dataclass
class AssessStep:
    """Recalculate the charge from the round, after the kernel's own I6 check."""

    pack: CasePack
    evidence: EvidenceStore
    assessments: AssessmentStore
    name: str = "assess"

    def run(self, case: Case, now: datetime) -> StepOutcome:
        bundle = self.evidence.bundle(case.id, case.gather_round)

        unavailable = _unavailable_module(bundle, self.pack, case)
        if unavailable is not None:
            # Before the pack is asked anything. I6 does not get to depend on the pack, or
            # on the reasoner after it, noticing that a module was off.
            return StepOutcome(
                trigger=Trigger.ESCALATE,
                review_reason=ReviewReason(
                    code=ReviewCode.MODULE_UNAVAILABLE,
                    module=unavailable,
                    detail=f"required evidence unavailable: {unavailable}",
                ),
                detail=f"{MODULE_UNAVAILABLE}{unavailable}",
            )

        # The claim the dispute is about. Without it the pack takes the first DEBIT in
        # the round, and a shipment with two charge lines recalculates one dispute
        # against the other's charge.
        assessment = self.pack.assess(
            case.id, case.gather_round, bundle, claim_ref=case.facts.claim_ref
        )
        self.assessments.add(assessment)

        if assessment.eligible == "ineligible":
            # S32: intake could not tell, the evidence round could. T05.
            return StepOutcome(
                trigger=Trigger.ASSESSMENT_INELIGIBLE,
                detail=", ".join(assessment.reason_codes),
            )
        return StepOutcome(trigger=Trigger.ASSESSMENT_READY)


def _unavailable_module(bundle: EvidenceBundle, pack: CasePack, case: Case) -> str | None:
    """The first module a *required* item failed on because it was switched off.

    Required only. An optional item that could not be fetched because its module is off is
    recorded and assessed around; stopping the case for a photo would send every dispute to
    a person the day the image service is turned off.
    """
    required = {r.key for r in pack.evidence_plan(case.facts) if r.required}
    for item in bundle.items:
        result = item.result
        code = result.error_code or ""
        if (
            result.status is EvidenceStatus.FAILED
            and result.requirement_key in required
            and code.startswith(MODULE_UNAVAILABLE)
        ):
            return code.removeprefix(MODULE_UNAVAILABLE)
    return None


__all__ = ["AssessStep", "CasePack", "EligibilityStep", "GatherEvidenceStep", "RetryTimers"]
