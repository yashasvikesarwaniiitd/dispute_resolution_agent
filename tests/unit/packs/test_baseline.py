"""Card P2-08: rows B1-B9, each with the situation that matches it and one that does not.

The "and one that does not" half is the half that earns its keep. A row tested only on its
match passes just as well when the condition is `True`, and a baseline that escalates
everything would satisfy six of these nine rows without doing anything at all.

Two tests here are about order rather than about a row, and they are the ones worth reading:

`TestB3SitsAboveB5` — a code-proven credit fires even when the round carries an image or a
reply. Reorder these two and the baseline silently stops crediting on roughly half the
fixtures, with every single-row test still green.

`TestB6SitsAboveB8` — a case inside tolerance that is still waiting on our own hub waits
rather than upholding, because upholding decides the case on evidence we have not seen.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    CaseState,
    Confidence,
    EvidenceStatus,
    FindingKind,
    ProposalAction,
    Provenance,
    ScreenKind,
)
from dispute_agent.kernel.domain.evidence import (
    ChargeAssessment,
    EvidenceBundle,
    EvidenceItem,
    EvidenceResult,
    Fact,
)
from dispute_agent.kernel.domain.ids import (
    AssessmentId,
    CaseId,
    EvidenceItemId,
    PayloadHash,
    ProposalId,
)
from dispute_agent.kernel.domain.reasoning import CaseBriefing, ContactStatus, ScreenResult
from dispute_agent.kernel.domain.values import Money
from dispute_agent.packs.weight_dispute.baseline import (
    B7_ITEMS,
    DECLARED_KEY,
    HISTORY_KEY,
    IMAGE_KEY,
    MEASURED_KEY,
    baseline_proposal,
    decide,
)

CASE = CaseId("case_p208")
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
HASH = PayloadHash("sha256:" + "a" * 64)
FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact="seller+1@example.invalid",
    raised_at=datetime(2026, 2, 1, tzinfo=UTC),
)

#: What the pack asks for. B1 turns entirely on this list.
REQUIRED = (DECLARED_KEY, MEASURED_KEY, "ledger.entries")

#: A box measured at exactly its declared size: inside any tolerance, so B8 territory.
SAME_SIZE = {"length_cm": "20", "width_cm": "15", "height_cm": "10"}
#: Half as long again. Beyond 5%, so B7 territory.
OVERSIZE = {"length_cm": "30", "width_cm": "15", "height_cm": "10"}
#: 3% longer. Inside 5%, so still B8.
JUST_INSIDE = {"length_cm": "20.6", "width_cm": "15", "height_cm": "10"}

ALL_ACTIONS = tuple(ProposalAction)


def item(
    key: str,
    *,
    status: EvidenceStatus = EvidenceStatus.FOUND,
    value: dict[str, Any] | None = None,
    provenance: Provenance = Provenance.SYSTEM,
    error_code: str = "timeout",
    round_: int = 1,
) -> EvidenceItem:
    return EvidenceItem(
        id=EvidenceItemId(f"evi_{key}_{status.value}"),
        case_id=CASE,
        gather_round=round_,
        provenance=provenance,
        result=EvidenceResult(
            requirement_key=key,
            source="evidence",
            status=status,
            value=value if status is EvidenceStatus.FOUND else None,
            error_code=error_code if status is EvidenceStatus.FAILED else None,
            retrieved_at=NOW,
        ),
        content_hash=HASH,
    )


def complete_items(measured: dict[str, Any] | None = None) -> list[EvidenceItem]:
    """Every required key FOUND, so nothing before B5 fires by accident."""
    return [
        item(DECLARED_KEY, value=SAME_SIZE),
        item(MEASURED_KEY, value=measured or SAME_SIZE),
        item("ledger.entries", value={"entries": []}),
    ]


def briefing(
    *,
    items: list[EvidenceItem] | None = None,
    definite_errors: tuple[str, ...] = (),
    allowed: tuple[ProposalAction, ...] = ALL_ACTIONS,
    reply_in_round: bool = False,
    remeasurement_outstanding: bool = False,
    screens: tuple[ScreenResult, ...] = (),
    gather_round: int = 1,
    facts: tuple[Fact, ...] = (),
) -> CaseBriefing:
    return CaseBriefing(
        case_id=CASE,
        state=CaseState.INVESTIGATING,
        facts=FACTS,
        evidence=EvidenceBundle(
            case_id=CASE,
            gather_round=gather_round,
            items=tuple(complete_items() if items is None else items),
        ),
        assessment=ChargeAssessment(
            id=AssessmentId("asm_1"),
            case_id=CASE,
            gather_round=gather_round,
            pack_version="1.0.0",
            eligible="eligible",
            charged=Money.of("190.00"),
            recalculated=Money.of("152.00") if definite_errors else Money.of("190.00"),
            discrepancy=Money.of("38.00") if definite_errors else Money.of("0.00"),
            definite_errors=definite_errors,
            facts=facts,
            input_hash=HASH,
        ),
        allowed_actions=allowed,
        screen_results=screens,
        contact=ContactStatus(
            reply_in_round=reply_in_round,
            clarifications_sent=0,
            remeasurement_outstanding=remeasurement_outstanding,
        ),
        briefing_hash=HASH,
    )


def row(**kwargs: Any) -> str:
    return decide(briefing(**kwargs), required_keys=REQUIRED).row


def action(**kwargs: Any) -> ProposalAction:
    return decide(briefing(**kwargs), required_keys=REQUIRED).action


class TestB1RequiredEvidenceMissing:
    def test_a_failed_required_item_escalates(self) -> None:
        items = complete_items()
        items[1] = item(MEASURED_KEY, status=EvidenceStatus.FAILED, error_code="timeout")
        assert row(items=items) == "B1"
        assert action(items=items) is ProposalAction.ESCALATE_TO_HUMAN

    def test_a_missing_required_item_escalates(self) -> None:
        items = complete_items()
        items[1] = item(MEASURED_KEY, status=EvidenceStatus.MISSING)
        assert row(items=items) == "B1"

    def test_a_required_item_that_never_came_back_escalates(self) -> None:
        """No row at all is the same situation as MISSING: the pack asked and has no answer."""
        assert row(items=complete_items()[:2]) == "B1"

    def test_a_failed_optional_item_does_not(self) -> None:
        """The non-matching half. An optional item that failed is recorded and ignored, or
        every case with no photo would reach a person."""
        items = [*complete_items(), item(IMAGE_KEY, status=EvidenceStatus.FAILED)]
        assert row(items=items) != "B1"

    def test_the_gap_is_named(self) -> None:
        items = complete_items()
        items[1] = item(MEASURED_KEY, status=EvidenceStatus.MISSING)
        decision = decide(briefing(items=items), required_keys=REQUIRED)
        assert MEASURED_KEY in decision.because


class TestB2SomethingIsSteeringUs:
    def test_a_screen_flag_escalates(self) -> None:
        flagged = (
            ScreenResult(kind=ScreenKind.SELLER_REPLY, flags=("instruction_attempt",), adapter="x"),
        )
        assert row(screens=flagged) == "B2"

    def test_a_blocked_screen_escalates(self) -> None:
        blocked = (ScreenResult(kind=ScreenKind.OPS_NOTE, blocked=True, adapter="x"),)
        assert row(screens=blocked) == "B2"

    def test_a_clean_screen_does_not(self) -> None:
        """I15's other half: a clean result never relaxes anything, but it must not stop
        the table either."""
        clean = (ScreenResult(kind=ScreenKind.SELLER_REPLY, adapter="x"),)
        assert row(screens=clean) != "B2"

    @pytest.mark.parametrize("intent", ["contains_instruction_attempt", "withdraws_dispute"])
    def test_a_stop_intent_in_a_reading_escalates(self, intent: str) -> None:
        reading = item("reply.reading", value={"intent": intent}, provenance=Provenance.AI_DERIVED)
        assert row(items=[*complete_items(), reading], reply_in_round=True) == "B2"

    def test_an_ordinary_reading_does_not(self) -> None:
        """It falls through to B5 instead, which is the row that owns "a reply needs
        judgement". Both escalate; conflating them would hide which rule did it."""
        reading = item(
            "reply.reading", value={"intent": "disagrees"}, provenance=Provenance.AI_DERIVED
        )
        assert row(items=[*complete_items(), reading], reply_in_round=True) == "B5"

    def test_a_reply_that_could_not_be_read_escalates(self) -> None:
        unread = item(
            "reply.reading", status=EvidenceStatus.FAILED, provenance=Provenance.AI_DERIVED
        )
        assert row(items=[*complete_items(), unread], reply_in_round=True) == "B2"

    def test_a_reply_with_no_reading_at_all_belongs_to_b5_not_here(self) -> None:
        """B2 is about a reading that was attempted and failed. Letting it claim every
        uninterpreted reply looks harmless and is not: until Phase 4 there is no
        interpreter, so every reply would be reported as tampering and B5 would never fire."""
        assert row(reply_in_round=True, items=complete_items()) == "B5"


class TestB3OneProvenError:
    def test_one_definite_error_proposes_a_credit(self) -> None:
        assert row(definite_errors=("calculation_error",)) == "B3"
        assert action(definite_errors=("calculation_error",)) is ProposalAction.PROPOSE_CREDIT

    def test_the_reason_code_is_the_proven_error(self) -> None:
        decision = decide(briefing(definite_errors=("duplicate_charge",)), required_keys=REQUIRED)
        assert decision.reason_code == "duplicate_charge"

    def test_no_definite_error_does_not(self) -> None:
        assert row(definite_errors=()) != "B3"

    def test_a_live_credit_on_the_line_does_not(self) -> None:
        """The menu is how the kernel rule arrives: a case with a live or verified credit
        has `propose_credit` taken off it, so B3 cannot fire and never credits twice (I3)."""
        menu = tuple(a for a in ALL_ACTIONS if a is not ProposalAction.PROPOSE_CREDIT)
        assert row(definite_errors=("calculation_error",), allowed=menu) != "B3"

    def test_it_cites_the_facts_the_amount_came_from(self) -> None:
        """A credit quotes facts by id, never by value (I9)."""
        facts = (Fact(fact_id="f_overcharge", label="overcharge", value=Money.of("38.00")),)
        decision = decide(
            briefing(definite_errors=("calculation_error",), facts=facts),
            required_keys=REQUIRED,
        )
        assert decision.evidence_refs == ("f_overcharge",)


class TestB4TwoProvenErrors:
    def test_two_errors_escalate(self) -> None:
        assert row(definite_errors=("calculation_error", "duplicate_charge")) == "B4"

    def test_one_does_not(self) -> None:
        assert row(definite_errors=("calculation_error",)) != "B4"

    def test_there_is_no_amount_on_the_decision(self) -> None:
        """Two proofs of overcharge are two different numbers (R14). Netting them
        automatically is how a seller gets paid twice for one mistake."""
        decision = decide(
            briefing(definite_errors=("calculation_error", "duplicate_charge")),
            required_keys=REQUIRED,
        )
        assert decision.reason_code is None


class TestB5TheLineTheBaselineDoesNotCross:
    """The property: the baseline never judges free text or images."""

    def test_a_reply_in_the_round_escalates(self) -> None:
        assert row(reply_in_round=True) == "B5"

    def test_an_image_item_escalates(self) -> None:
        items = [*complete_items(), item(IMAGE_KEY, value={"description": "one box"})]
        assert row(items=items) == "B5"

    def test_a_history_item_escalates(self) -> None:
        items = [*complete_items(), item(HISTORY_KEY, value={"entries": ["a", "b"]})]
        assert row(items=items) == "B5"

    def test_a_round_with_none_of_those_does_not(self) -> None:
        assert row() != "B5"

    def test_a_failed_image_item_is_not_judgement(self) -> None:
        """Nothing to judge. An item that failed is not a picture."""
        items = [*complete_items(), item(IMAGE_KEY, status=EvidenceStatus.FAILED)]
        assert row(items=items) != "B5"


class TestB3SitsAboveB5:
    """The ordering that would break silently. agent-design.md section 5, the Watch note."""

    def test_a_proven_credit_fires_through_an_image(self) -> None:
        items = [*complete_items(), item(IMAGE_KEY, value={"description": "one box"})]
        assert row(items=items, definite_errors=("calculation_error",)) == "B3"

    def test_a_proven_credit_fires_through_a_reply(self) -> None:
        assert row(reply_in_round=True, definite_errors=("calculation_error",)) == "B3"

    def test_but_not_through_a_screen_flag(self) -> None:
        """B2 is above B3: something that may be steering us outranks even proven money."""
        flagged = (ScreenResult(kind=ScreenKind.SELLER_REPLY, blocked=True, adapter="x"),)
        assert row(screens=flagged, definite_errors=("calculation_error",)) == "B2"

    def test_without_a_proven_error_the_image_escalates(self) -> None:
        """The control: B5 really is doing something on that same round."""
        items = [*complete_items(), item(IMAGE_KEY, value={"description": "one box"})]
        assert row(items=items, definite_errors=()) == "B5"


class TestB6AnOutstandingRemeasurement:
    def test_it_waits(self) -> None:
        assert row(remeasurement_outstanding=True) == "B6"
        assert action(remeasurement_outstanding=True) is ProposalAction.WAIT

    def test_no_outstanding_remeasurement_does_not(self) -> None:
        assert row(remeasurement_outstanding=False) != "B6"


class TestB6SitsAboveB8:
    """Do not uphold while our own hub is still measuring (CLAUDE.md section 6, fail safe)."""

    def test_a_case_inside_tolerance_waits_rather_than_upholding(self) -> None:
        inside = complete_items(measured=JUST_INSIDE)
        assert row(items=inside, remeasurement_outstanding=True) == "B6"

    def test_the_same_case_upholds_once_nothing_is_outstanding(self) -> None:
        inside = complete_items(measured=JUST_INSIDE)
        assert row(items=inside, remeasurement_outstanding=False) == "B8"


class TestB7AskTheSeller:
    def test_an_oversize_box_in_the_first_round_asks_for_evidence(self) -> None:
        oversize = complete_items(measured=OVERSIZE)
        assert row(items=oversize) == "B7"
        assert action(items=oversize) is ProposalAction.REQUEST_SELLER_EVIDENCE

    def test_it_asks_for_the_two_documented_items(self) -> None:
        decision = decide(briefing(items=complete_items(measured=OVERSIZE)), required_keys=REQUIRED)
        assert decision.requested_items == B7_ITEMS

    def test_a_box_within_tolerance_does_not(self) -> None:
        assert row(items=complete_items(measured=JUST_INSIDE)) != "B7"

    def test_a_later_round_does_not_ask_again(self) -> None:
        """ "No evidence request sent yet". A second ask is a clarification, which is
        policy's business and capped at one (D19)."""
        oversize = complete_items(measured=OVERSIZE)
        assert row(items=oversize, gather_round=2) != "B7"

    def test_a_proven_error_does_not_ask_for_a_photo(self) -> None:
        """B3 already decided it. Asking for a photo of a box whose bill was miscalculated
        delays a credit the arithmetic settled."""
        oversize = complete_items(measured=OVERSIZE)
        assert row(items=oversize, definite_errors=("calculation_error",)) == "B3"


class TestB8UpholdTheCharge:
    def test_a_correct_charge_on_a_box_the_declared_size_upholds(self) -> None:
        assert row() == "B8"
        assert action() is ProposalAction.UPHOLD_CHARGE

    def test_three_percent_over_is_still_within_tolerance(self) -> None:
        assert row(items=complete_items(measured=JUST_INSIDE)) == "B8"

    def test_an_oversize_box_does_not_uphold(self) -> None:
        assert row(items=complete_items(measured=OVERSIZE)) != "B8"

    def test_a_live_credit_on_the_line_does_not_uphold(self) -> None:
        """A reopened credited case can never tell the seller the charge stands after money
        moved. The menu is where that rule arrives."""
        menu = tuple(a for a in ALL_ACTIONS if a is not ProposalAction.UPHOLD_CHARGE)
        assert row(allowed=menu) == "B9"

    def test_the_tolerance_is_configuration(self) -> None:
        over = briefing(items=complete_items(measured=JUST_INSIDE))
        assert decide(over, required_keys=REQUIRED, tolerance_pct=1).row == "B7"
        assert decide(over, required_keys=REQUIRED, tolerance_pct=5).row == "B8"


class TestB9TheCatchAll:
    def test_unreadable_dimensions_fall_through_rather_than_upholding(self) -> None:
        """A measurement that cannot be read must not read as "within tolerance". Upholding
        a charge nobody checked is the worst available answer."""
        items = complete_items()
        items[0] = item(DECLARED_KEY, value={"length_cm": "not a number"})
        assert row(items=items) == "B9"

    def test_the_table_is_total(self) -> None:
        """Every path out of `decide` is one of the nine rows, so there is no input the
        baseline has no answer for."""
        assert row(allowed=(ProposalAction.ESCALATE_TO_HUMAN,)) == "B9"


class TestTheProposalItBuilds:
    def test_confidence_is_always_high(self) -> None:
        """The table is deterministic, so there is nothing to be uncertain about - and R22
        therefore never fires on a baseline proposal (section 15.2)."""
        for kwargs in ({}, {"reply_in_round": True}, {"definite_errors": ("duplicate_charge",)}):
            proposal = baseline_proposal(
                briefing(**kwargs),  # type: ignore[arg-type]
                proposal_id=ProposalId("prp_1"),
                now=NOW,
                required_keys=REQUIRED,
            )
            assert proposal.confidence is Confidence.HIGH

    def test_there_is_exactly_one_verified_fact_naming_the_row(self) -> None:
        proposal = baseline_proposal(
            briefing(reply_in_round=True),
            proposal_id=ProposalId("prp_1"),
            now=NOW,
            required_keys=REQUIRED,
        )
        assert len(proposal.findings) == 1
        assert proposal.findings[0].kind is FindingKind.VERIFIED_FACT
        assert proposal.findings[0].statement.startswith("B5")

    def test_pattern_influence_is_all_false(self) -> None:
        """The baseline never reads pattern_context, so I7 holds trivially on this path."""
        proposal = baseline_proposal(
            briefing(), proposal_id=ProposalId("prp_1"), now=NOW, required_keys=REQUIRED
        )
        assert proposal.pattern_influence.considered is False
        assert proposal.pattern_influence.changed_recommendation is False
        assert proposal.pattern_influence.note == ""

    def test_it_names_itself_as_rules_only_and_carries_no_prompt(self) -> None:
        """The prompt fields are set exactly when the reasoner is the model. A baseline
        proposal claiming a prompt hash would make the audit trail a lie."""
        proposal = baseline_proposal(
            briefing(), proposal_id=ProposalId("prp_1"), now=NOW, required_keys=REQUIRED
        )
        assert proposal.reasoner == "rules_only"
        assert proposal.prompt_name is None
        assert proposal.prompt_hash is None
        assert proposal.model_id is None

    def test_the_fallback_flag_is_the_caller_s_to_set(self) -> None:
        """Same table either way; what differs is whether the model was meant to run."""
        proposal = baseline_proposal(
            briefing(),
            proposal_id=ProposalId("prp_1"),
            now=NOW,
            required_keys=REQUIRED,
            fallback_used=True,
        )
        assert proposal.fallback_used is True

    def test_the_credit_reason_reaches_the_parameters(self) -> None:
        proposal = baseline_proposal(
            briefing(definite_errors=("remeasurement_lower",)),
            proposal_id=ProposalId("prp_1"),
            now=NOW,
            required_keys=REQUIRED,
        )
        assert proposal.action is ProposalAction.PROPOSE_CREDIT
        assert proposal.action_parameters.reason_code == "remeasurement_lower"

    def test_it_is_deterministic(self) -> None:
        """Same briefing in, same proposal out, which is what makes it a bar to measure
        the model against (ADR 0024)."""
        args = {"proposal_id": ProposalId("prp_1"), "now": NOW, "required_keys": REQUIRED}
        first = baseline_proposal(briefing(), **args)  # type: ignore[arg-type]
        second = baseline_proposal(briefing(), **args)  # type: ignore[arg-type]
        assert first == second
