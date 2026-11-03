"""Card P1-13 and ADR 0026: the escalation out of GATHERING_EVIDENCE is real.

The Stage A4 review found no scenario reaching this edge and asked whether it was dead
design. It is not — it is simply not an *evidence* transition. An evidence failure becomes a
FAILED item that a later step reads; what reaches T90 from here are the things that can go
wrong regardless of what the step was doing.

Each test below is one of those causes, walked through the real table.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.transitions import default_table
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    CaseState,
    CauseKind,
    ReviewCode,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.events import StateChangedPayload
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
from dispute_agent.kernel.domain.models import Case, Cause, ReviewReason
from dispute_agent.kernel.orchestrator.orchestrator import Orchestrator, StepOutcome
from dispute_agent.kernel.statemachine.machine import StateMachine

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
TABLE = default_table()
MACHINE = StateMachine(TABLE)
STEP = Cause(kind=CauseKind.STEP, ref="gather_evidence")
INPUT = EventId("evt_input0000000000000000000")

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)


def gathering() -> Case:
    return Case(
        id=CaseId("case_01HQ"),
        pack="weight_dispute",
        facts=FACTS,
        state=CaseState.GATHERING_EVIDENCE,
        version=2,
        correlation_id=CorrelationId("corr_01HQ"),
        opened_at=NOW,
        updated_at=NOW,
        state_entered_at=NOW,
        gather_round=1,
    )


def escalate(reason: ReviewReason):
    return MACHINE.apply(
        gathering(),
        Trigger.ESCALATE,
        STEP,
        NOW,
        EventId("evt_01HQ"),
        INPUT,
        guards=dict.fromkeys(TABLE.guards, True),
        review_reason=reason,
    )


class TestTheEdgeExists:
    def test_the_table_declares_it(self) -> None:
        assert CaseState.GATHERING_EVIDENCE in TABLE.groups["escalable"]
        found = TABLE.find(CaseState.GATHERING_EVIDENCE, Trigger.ESCALATE)
        assert found is not None
        assert found.id == "T90"
        assert found.to_state is CaseState.NEEDS_HUMAN_REVIEW

    def test_the_database_allows_the_pair(self) -> None:
        """Removing it from the table alone would leave schema.sql disagreeing."""
        assert (
            CaseState.GATHERING_EVIDENCE,
            CaseState.NEEDS_HUMAN_REVIEW,
        ) in TABLE.legal_pairs()


class TestTheCausesThatReachIt:
    """Four reasons, none of them an evidence failure (ADR 0026)."""

    def test_the_kill_switch_strands_a_gathering_case(self) -> None:
        """I10. Without this edge, a switch thrown mid-gather leaves no legal move at all."""
        applied = escalate(ReviewReason(code=ReviewCode.KILL_SWITCH_ACTIVE))
        assert applied.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert applied.transition.id == "T90"

    def test_the_step_limit_reaches_it(self) -> None:
        applied = escalate(ReviewReason(code=ReviewCode.STEP_LIMIT, detail="12 steps"))
        assert applied.case.review_reason is not None
        assert applied.case.review_reason.code is ReviewCode.STEP_LIMIT

    def test_a_disabled_module_reaches_it(self) -> None:
        """I6: gather_evidence needs the evidence module, and may not have it."""
        applied = escalate(ReviewReason(code=ReviewCode.MODULE_UNAVAILABLE, module="evidence"))
        assert applied.case.review_reason is not None
        assert applied.case.review_reason.render() == "module_unavailable:evidence"

    def test_an_internal_error_reaches_it(self) -> None:
        applied = escalate(ReviewReason(code=ReviewCode.INTERNAL_ERROR, detail="bug"))
        assert applied.case.state is CaseState.NEEDS_HUMAN_REVIEW


class TestItIsWalkedEndToEnd:
    def test_a_gathering_step_raising_module_unavailable_escalates(self) -> None:
        """Through the orchestrator, not just the machine: the whole path is real."""

        class NeedsEvidence:
            name = "gather_evidence"

            def run(self, case: Case, now: datetime) -> StepOutcome:
                raise ModuleUnavailable("evidence")

        loop = Orchestrator(
            MACHINE, {"gather_evidence": NeedsEvidence()}, FakeClock(NOW), FakeIdGenerator()
        )
        result = loop.advance(gathering(), INPUT)

        assert result.stopped_because == "module_unavailable"
        assert result.case.state is CaseState.NEEDS_HUMAN_REVIEW
        change = result.events[-1].payload
        assert isinstance(change, StateChangedPayload)
        assert change.transition_id == "T90"
        assert change.from_state is CaseState.GATHERING_EVIDENCE

    def test_the_effects_fire_too(self) -> None:
        """T90 cancels every timer and marks in-flight actions UNKNOWN."""
        applied = escalate(ReviewReason(code=ReviewCode.STEP_LIMIT))
        assert applied.effects.cancel_all_timers is True
        assert applied.effects.mark_in_flight_unknown is True


class TestWhyNoEvidenceScenarioReachesIt:
    def test_an_evidence_failure_is_an_item_not_an_escalation(self) -> None:
        """orchestrator.md section 5. This is what the A4 review was looking at, and it is
        why no evidence scenario walks the edge — correctly."""
        from dispute_agent.kernel.domain.enums import EvidenceStatus

        assert EvidenceStatus.FAILED.value == "FAILED"
        # A failed fetch produces an item the assess step reads; the case stays where it is.
        assert TABLE.find(CaseState.GATHERING_EVIDENCE, Trigger.EVIDENCE_RECORDED) is not None

    @pytest.mark.skip(
        reason="the kill-switch-while-gathering scenario lands with gather_evidence in "
        "Phase 2 (card P2-04); ADR 0026 records why it is not written yet"
    )
    def test_scenario_kill_switch_while_gathering(self) -> None: ...
