"""Card P2-06: the gather round. Scenarios S05a, S05c, S21, and I4 throughout.

The file is built around one question — *can a round ever produce a value nobody fetched?* —
asked in as many ways as there are to fail: a source that times out, one that is switched
off, one nobody registered, one that lies about having found something. `test_never_invents`
asks it of every one of them at once, which is the version that keeps working when somebody
adds a fourth failure mode.

The retry arithmetic (D41) is tested on the count rather than on the clock, because the
clock is not what goes wrong. What goes wrong is counting per case instead of per round
(finding B22), so a case that spent its retries in round 1 never retries a genuine blip in
round 2 again. `TestTheLimitIsPerRound` is that test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.errors import DomainValidationError, ModuleUnavailable
from dispute_agent.kernel.domain.evidence import EvidenceRequirement, EvidenceResult
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.orchestrator.evidence import (
    GatherOutcome,
    gather,
    is_retryable,
    module_unavailable_code,
)
from dispute_agent.kernel.policy.config import TimersConfig

CASE = CaseId("case_p206")
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact="seller+1@example.invalid",
    raised_at=datetime(2026, 2, 1, tzinfo=UTC),
)

DIMS = EvidenceRequirement(key="shipment.measured_dims", source="evidence", required=True)
LEDGER = EvidenceRequirement(key="ledger.entries", source="evidence", required=True)
PHOTO = EvidenceRequirement(key="shipment.image_description", source="evidence", required=False)


class FakeSource:
    """Answers each key however the test says, and counts attempts.

    A scripted list per key is what makes "recovers on the second retry" testable at all: a
    source that is always broken or always fine would let a retry loop that never actually
    retries pass every test in this file.
    """

    name = "evidence"

    def __init__(self, script: dict[str, list[object]] | None = None) -> None:
        self.script = script or {}
        self.calls: dict[str, int] = {}

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        key = requirement.key
        attempt = self.calls.get(key, 0)
        self.calls[key] = attempt + 1
        steps = self.script.get(key)
        answer = steps[min(attempt, len(steps) - 1)] if steps else "found"

        if answer == "unavailable":
            raise ModuleUnavailable("evidence")
        if isinstance(answer, str) and answer != "found":
            return EvidenceResult(
                requirement_key=key,
                source=self.name,
                status=EvidenceStatus.FAILED,
                error_code=answer,
                retrieved_at=NOW,
            )
        return EvidenceResult(
            requirement_key=key,
            source=self.name,
            status=EvidenceStatus.FOUND,
            value={"ok": True},
            retrieved_at=NOW,
        )


def run(
    source: FakeSource | None = None,
    *,
    requirements: list[EvidenceRequirement] | None = None,
    sources: dict[str, object] | None = None,
    retries: int = 0,
    timers: TimersConfig | None = None,
) -> GatherOutcome:
    registry = sources if sources is not None else {"evidence": source or FakeSource()}
    return gather(
        case_id=CASE,
        facts=FACTS,
        requirements=requirements or [DIMS, LEDGER],
        sources=registry,  # type: ignore[arg-type]
        now=NOW,
        retries_fired_this_round=retries,
        timers=timers,
    )


class TestAGoodRound:
    def test_every_requirement_gets_a_result(self) -> None:
        outcome = run()
        assert [r.requirement_key for r in outcome.results] == [DIMS.key, LEDGER.key]

    def test_a_clean_round_does_not_rest(self) -> None:
        outcome = run()
        assert not outcome.rests
        assert outcome.retry_due_at is None

    def test_results_keep_the_plan_order(self) -> None:
        """So a reader of the round sees it in the order the pack asked for it."""
        outcome = run(requirements=[PHOTO, DIMS, LEDGER])
        assert [r.requirement_key for r in outcome.results] == [PHOTO.key, DIMS.key, LEDGER.key]


class TestNeverInvents:
    """I4, asked of every way a fetch can go wrong."""

    @pytest.mark.parametrize(
        "fault", ["timeout", "error_permanent", "unavailable", "something_nobody_classified"]
    )
    def test_no_failure_mode_produces_a_value(self, fault: str) -> None:
        outcome = run(FakeSource({DIMS.key: [fault], LEDGER.key: [fault]}), retries=99)
        assert outcome.invented_values() == ()
        assert all(r.status is EvidenceStatus.FAILED for r in outcome.results)

    def test_with_every_source_failing_no_item_carries_a_value(self) -> None:
        outcome = run(sources={}, retries=99)
        assert outcome.invented_values() == ()
        assert all(r.value is None for r in outcome.results)

    def test_a_source_cannot_even_build_a_found_result_with_no_value(self) -> None:
        """The strongest form of I4 available: a source claiming FOUND with nothing in hand
        is not caught by the gather round, it is unconstructible. `EvidenceResult` sets a
        value exactly when the status is FOUND, so the empty-but-found item the calculator
        would have to defend against never exists to be passed on."""
        with pytest.raises(DomainValidationError, match="exactly when status is FOUND"):
            EvidenceResult(
                requirement_key=DIMS.key,
                source="evidence",
                status=EvidenceStatus.FOUND,
                value=None,
                retrieved_at=NOW,
            )

    def test_a_missing_item_is_not_a_failure(self) -> None:
        """MISSING is a fact about the shipment; FAILED is a fact about the fetch."""
        missing = EvidenceResult(
            requirement_key=PHOTO.key,
            source="evidence",
            status=EvidenceStatus.MISSING,
            retrieved_at=NOW,
        )

        class Empty(FakeSource):
            def fetch(self, c: CaseId, f: CaseFacts, r: EvidenceRequirement) -> EvidenceResult:
                return missing

        outcome = run(Empty(), requirements=[PHOTO])
        assert outcome.failures() == ()
        assert not outcome.rests


class TestTransientFailureRetries:
    """S05a: three retries 15 minutes apart, then the round is recorded with the failure."""

    def test_a_required_transient_failure_rests_and_arms_a_retry(self) -> None:
        outcome = run(FakeSource({DIMS.key: ["timeout"]}))
        assert outcome.rests
        assert outcome.retry_due_at == NOW + timedelta(minutes=15)
        assert DIMS.key in outcome.rest_reason

    def test_the_interval_comes_from_configuration(self) -> None:
        outcome = run(
            FakeSource({DIMS.key: ["timeout"]}), timers=TimersConfig(evidence_retry_minutes=5)
        )
        assert outcome.retry_due_at == NOW + timedelta(minutes=5)

    @pytest.mark.parametrize("fired", [0, 1, 2])
    def test_it_keeps_retrying_up_to_the_limit(self, fired: int) -> None:
        assert run(FakeSource({DIMS.key: ["timeout"]}), retries=fired).rests

    def test_after_the_third_retry_the_round_is_recorded_with_the_failure(self) -> None:
        """S05a's ending: the case moves on with a FAILED item rather than waiting forever."""
        outcome = run(FakeSource({DIMS.key: ["timeout"]}), retries=3)
        assert not outcome.rests
        assert [r.status for r in outcome.failures()] == [EvidenceStatus.FAILED]
        assert outcome.failures()[0].error_code == "timeout"

    def test_the_failed_item_is_still_in_the_round(self) -> None:
        """It travels to `assess` and to the reasoner. Dropping it would read as "we never
        needed that", which is how an outage becomes a fact about the shipment."""
        outcome = run(FakeSource({DIMS.key: ["timeout"]}), retries=3)
        assert DIMS.key in {r.requirement_key for r in outcome.results}


class TestRecoveringOnARetry:
    """S05c: fails, fails, then works, and the round continues normally."""

    def test_the_third_attempt_succeeds_and_the_round_does_not_rest(self) -> None:
        source = FakeSource({DIMS.key: ["timeout", "timeout", "found"]})
        assert run(source, retries=0).rests
        assert run(source, retries=1).rests
        final = run(source, retries=2)
        assert not final.rests
        assert final.failures() == ()

    def test_a_source_that_never_recovers_is_the_control(self) -> None:
        """Without this, a retry loop that silently stopped calling the source would pass
        the test above."""
        source = FakeSource({DIMS.key: ["timeout"]})
        assert run(source, retries=2).rests
        assert source.calls[DIMS.key] == 1


class TestPermanentFailuresDoNotRetry:
    """S21."""

    def test_a_permanent_error_records_at_once(self) -> None:
        outcome = run(FakeSource({DIMS.key: ["error_permanent"]}))
        assert not outcome.rests
        assert outcome.retry_due_at is None
        assert outcome.failures()[0].error_code == "error_permanent"

    def test_a_switched_off_module_does_not_retry(self) -> None:
        """Three retries over 45 minutes for something that was never going to succeed."""
        outcome = run(FakeSource({DIMS.key: ["unavailable"]}))
        assert not outcome.rests

    def test_an_unclassified_code_does_not_retry(self) -> None:
        """Erring towards one extra trip to a person, never towards hammering."""
        assert not run(FakeSource({DIMS.key: ["gateway_exploded"]})).rests

    def test_is_retryable_agrees_with_all_of_that(self) -> None:
        assert is_retryable("timeout")
        assert not is_retryable("error_permanent")
        assert not is_retryable(None)
        assert not is_retryable(module_unavailable_code("evidence"))
        assert not is_retryable(module_unavailable_code("timeout"))


class TestOnlyRequiredItemsHoldTheCaseUp:
    def test_an_optional_timeout_does_not_rest_the_round(self) -> None:
        """45 minutes of waiting for something the assessment does not need."""
        outcome = run(FakeSource({PHOTO.key: ["timeout"]}), requirements=[DIMS, PHOTO])
        assert not outcome.rests
        assert outcome.failures()[0].requirement_key == PHOTO.key

    def test_a_required_timeout_alongside_it_does(self) -> None:
        outcome = run(
            FakeSource({PHOTO.key: ["timeout"], DIMS.key: ["timeout"]}),
            requirements=[PHOTO, DIMS],
        )
        assert outcome.rests
        assert DIMS.key in outcome.rest_reason


class TestASwitchedOffModuleIsRecordedNotRaised:
    """I6, reached one step later and by code rather than by the reasoner."""

    def test_module_unavailable_becomes_a_failed_item(self) -> None:
        outcome = run(FakeSource({DIMS.key: ["unavailable"]}))
        assert outcome.failures()[0].error_code == module_unavailable_code("evidence")

    def test_it_does_not_escape_as_an_exception(self) -> None:
        """Letting it out would hit the orchestrator's conversion point and lose the round:
        the case would reach a person with no record of what else succeeded."""
        outcome = run(FakeSource({DIMS.key: ["unavailable"], LEDGER.key: ["found"]}))
        assert outcome.results[1].status is EvidenceStatus.FOUND

    def test_the_module_name_survives_for_assess_to_escalate_with(self) -> None:
        outcome = run(FakeSource({DIMS.key: ["unavailable"]}))
        assert outcome.unavailable_modules() == ("evidence",)

    def test_a_source_nobody_registered_is_the_same_situation(self) -> None:
        """From the case's point of view: the thing that was to answer this cannot. A
        KeyError out of the step would lose the whole round to internal_error."""
        outcome = run(sources={})
        assert outcome.unavailable_modules() == ("evidence",)

    def test_a_clean_round_names_no_unavailable_module(self) -> None:
        assert run().unavailable_modules() == ()


class TestTheLimitIsPerRound:
    """Finding B22, which is the bug this count is shaped to avoid."""

    def test_a_fresh_round_starts_with_its_retries_intact(self) -> None:
        """Round 1 spent all three. Round 2 passes 0, because fired rows are counted by
        `timers.gather_round` and not by case. Counting per case would mean a case that had
        one bad afternoon never retries a genuine blip again."""
        spent = run(FakeSource({DIMS.key: ["timeout"]}), retries=3)
        fresh = run(FakeSource({DIMS.key: ["timeout"]}), retries=0)
        assert not spent.rests
        assert fresh.rests

    def test_the_limit_comes_from_configuration(self) -> None:
        timers = TimersConfig(evidence_max_retries=1)
        assert run(FakeSource({DIMS.key: ["timeout"]}), retries=0, timers=timers).rests
        assert not run(FakeSource({DIMS.key: ["timeout"]}), retries=1, timers=timers).rests
