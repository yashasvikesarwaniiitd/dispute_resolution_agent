"""Card P0-04: the cross-case socket models.

The point of this file is I7. Cross-case intelligence is the one part of the system that
looks at other people's cases, and the invariant says it may only ever make the system more
careful. The tests that matter here are the ones asserting that lowering a verdict is
*unrepresentable* — not refused at runtime by a rule someone could later delete, but absent
from the type, so no adapter in Phase 7 can express it at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import CaseState, VerdictKind
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import CaseId, OutcomeId, PatternId
from dispute_agent.kernel.domain.patterns import (
    MAX_PATTERN_SUMMARY_CHARS,
    MAX_SIGNAL_REASON_CHARS,
    CaseOutcome,
    CautionSignal,
    PatternRecord,
)
from dispute_agent.kernel.domain.values import Money

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


def a_pattern(**overrides: object) -> PatternRecord:
    fields: dict[str, object] = {
        "id": PatternId("pat_01HQ"),
        "status": "UNCONFIRMED",
        "summary": "Seven disputes at HUB-07 share a volumetric re-measure above the declared dims.",
        "matched_on": {"hub": "HUB-07"},
        "related_case_ids": (CaseId("case_01HQ"), CaseId("case_01HR")),
        "first_seen_at": NOW,
        "last_seen_at": NOW,
    }
    fields.update(overrides)
    return PatternRecord(**fields)


def a_signal(**overrides: object) -> CautionSignal:
    fields: dict[str, object] = {
        "kind": "pattern_match",
        "min_verdict": VerdictKind.REQUIRE_APPROVAL,
        "reason": "matches pattern pat_01HQ (HUB-07 volumetric re-measure)",
        "source_ref": "pat_01HQ",
    }
    fields.update(overrides)
    return CautionSignal(**fields)


FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact="seller@example.invalid",
    raised_at=NOW,
)


def an_outcome(**overrides: object) -> CaseOutcome:
    fields: dict[str, object] = {
        "id": OutcomeId("out_01HQ"),
        "case_id": CaseId("case_01HQ"),
        "facts": FACTS,
        "state": CaseState.RESOLVED_UPHELD,
        "resolved": True,
        "assessment_id": None,
        "credited": None,
        "used_pattern_ids": (),
        "closed_at": NOW,
        "reopen_count": 0,
    }
    fields.update(overrides)
    return CaseOutcome(**fields)


class TestCautionSignalCannotLowerAVerdict:
    """I7 and I15. These are the tests the whole card exists for."""

    def test_allow_is_not_a_representable_minimum(self) -> None:
        """ALLOW would mean "this pattern says go ahead", which I7 forbids outright."""
        with pytest.raises(ValidationError):
            a_signal(min_verdict=VerdictKind.ALLOW)

    def test_deny_is_not_a_representable_minimum(self) -> None:
        """DENY is a decision, not caution: only a policy rule may refuse an action."""
        with pytest.raises(ValidationError):
            a_signal(min_verdict=VerdictKind.DENY)

    def test_there_is_no_field_that_expresses_a_maximum(self) -> None:
        """Structural, not behavioural.

        A `max_verdict` field would let a signal cap how cautious the system becomes, which
        is lowering a verdict by another name. The guarantee is that no such field exists,
        so this reads the model rather than exercising it.
        """
        fields = set(CautionSignal.model_fields)
        assert fields == {"kind", "min_verdict", "reason", "source_ref"}
        assert not [f for f in fields if "max" in f or "ceiling" in f or "cap" in f]

    def test_both_permitted_minimums_raise_rather_than_lower(self) -> None:
        for verdict in (VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE):
            assert a_signal(min_verdict=verdict).min_verdict > VerdictKind.ALLOW

    def test_an_unknown_kind_is_refused(self) -> None:
        """The five kinds are a closed set; a new one is a design decision, not a string."""
        with pytest.raises(ValidationError):
            a_signal(kind="pattern_says_approve")

    def test_reason_is_capped(self) -> None:
        a_signal(reason="r" * MAX_SIGNAL_REASON_CHARS)
        with pytest.raises(ValidationError):
            a_signal(reason="r" * (MAX_SIGNAL_REASON_CHARS + 1))

    def test_frozen(self) -> None:
        """Assigning ALLOW here would be the obvious test, and mypy refuses to compile it:
        the field's type is Literal[REQUIRE_APPROVAL, ESCALATE], so lowering a verdict is
        caught statically as well as at construction. This asserts only the freeze."""
        signal = a_signal()
        with pytest.raises(ValidationError):
            signal.min_verdict = VerdictKind.ESCALATE


class TestPatternRecord:
    def test_summary_is_capped_at_four_hundred(self) -> None:
        """The cap is a prompt-budget commitment as much as a storage one (agent-design 1)."""
        assert MAX_PATTERN_SUMMARY_CHARS == 400
        a_pattern(summary="s" * 400)
        with pytest.raises(ValidationError):
            a_pattern(summary="s" * 401)

    def test_summary_is_not_truncated_silently(self) -> None:
        """Unlike UntrustedText, which truncates and records it, an over-long summary is a
        programming error upstream: the producer owns the cap, so failing is honest."""
        with pytest.raises(ValidationError):
            a_pattern(summary="s" * 500)

    def test_status_is_a_closed_set(self) -> None:
        for status in ("UNCONFIRMED", "CONFIRMED", "DISMISSED"):
            assert a_pattern(status=status).status == status
        with pytest.raises(ValidationError):
            a_pattern(status="PROVEN")

    def test_timestamps_must_be_aware(self) -> None:
        with pytest.raises(DomainValidationError):
            a_pattern(first_seen_at=datetime(2026, 3, 1, 9, 30))

    def test_last_seen_cannot_precede_first_seen(self) -> None:
        with pytest.raises(DomainValidationError):
            a_pattern(last_seen_at=datetime(2026, 2, 1, tzinfo=UTC))

    def test_frozen(self) -> None:
        pattern = a_pattern()
        with pytest.raises(ValidationError):
            pattern.status = "CONFIRMED"


class TestCaseOutcome:
    def test_state_must_be_terminal(self) -> None:
        """HOOK C fires when a case closes; an open case has no outcome to report."""
        with pytest.raises(DomainValidationError):
            an_outcome(state=CaseState.INVESTIGATING, resolved=False)

    @pytest.mark.parametrize("state", [CaseState.RESOLVED_CREDITED, CaseState.RESOLVED_UPHELD])
    def test_resolved_is_true_exactly_for_the_two_resolved_states(self, state: CaseState) -> None:
        an_outcome(state=state, resolved=True)
        with pytest.raises(DomainValidationError):
            an_outcome(state=state, resolved=False)

    @pytest.mark.parametrize(
        "state",
        [
            CaseState.WITHDRAWN,
            CaseState.CLOSED_NO_RESPONSE,
            CaseState.REJECTED_INELIGIBLE,
            CaseState.HANDED_TO_HUMAN,
        ],
    )
    def test_the_other_terminal_states_are_not_resolved(self, state: CaseState) -> None:
        an_outcome(state=state, resolved=False)
        with pytest.raises(DomainValidationError):
            an_outcome(state=state, resolved=True)

    def test_credit_total_is_money(self) -> None:
        outcome = an_outcome(state=CaseState.RESOLVED_CREDITED, credited=Money.of("1820.00"))
        assert outcome.credited == Money.of("1820.00")

    def test_used_pattern_ids_defaults_to_empty(self) -> None:
        """Phases 0 to 6: every case closes having used no pattern at all (FLOW 'empty box')."""
        assert an_outcome().used_pattern_ids == ()

    def test_reopen_count_cannot_be_negative(self) -> None:
        with pytest.raises(ValidationError):
            an_outcome(reopen_count=-1)

    def test_frozen(self) -> None:
        outcome = an_outcome()
        with pytest.raises(ValidationError):
            outcome.resolved = False
