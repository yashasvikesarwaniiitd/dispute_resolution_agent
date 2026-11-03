"""R09: no number reaches a seller unless code computed it (policy-rules.md 7, I9).

The negative tests the catalogue names are the point of this file. Each is a different way a
wrong figure gets into an email, and the one that matters most is the third: a *correct*
number cited against the *wrong* fact. That passes any check built on "is this number in the
facts somewhere", and it is how a weight ends up written as a charge.

The identifier masking is tested as carefully as the denials. A rule that failed every
message on the digits inside its own case reference would be switched off within a day, and
then nothing would be checking numbers at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from dispute_agent.kernel.domain.actions import ActionIntent, MessagePayload
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionType,
    CaseState,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
    VerdictKind,
)
from dispute_agent.kernel.domain.evidence import ChargeAssessment, Fact
from dispute_agent.kernel.domain.ids import (
    AssessmentId,
    CaseId,
    CorrelationId,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Case
from dispute_agent.kernel.domain.reasoning import NumberUse
from dispute_agent.kernel.domain.values import Money, Weight
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.rules import MessageNumbersVerified
from dispute_agent.kernel.policy.verdicts import PolicyContext

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
SELLER = "seller@example.invalid"
HASH = PayloadHash("sha256:" + "a" * 64)

FACTS = CaseFacts(
    shipment_ref="SHP-000001",
    claim_ref="CLM-000001",
    seller_ref="SELL-0001",
    seller_contact=SELLER,
    raised_at=NOW,
)
CASE = Case(
    id=CaseId("case_r09"),
    pack="weight_dispute",
    facts=FACTS,
    state=CaseState.INVESTIGATING,
    version=1,
    correlation_id=CorrelationId("corr_r09"),
    opened_at=NOW,
    updated_at=NOW,
    state_entered_at=NOW,
)
POLICY = PolicyConfig(messages=MessagesConfig(allowed_recipients=frozenset({SELLER})))

CHARGED = Fact(fact_id="f_charged", label="charged", value=Money.of("152.00"))
GRAMS = Fact(fact_id="f_grams", label="chargeable", value=Weight.from_grams(1980))
DIMENSION = Fact(fact_id="f_length", label="length", value=Decimal("30"))


def assessment(*facts: Fact) -> ChargeAssessment:
    return ChargeAssessment(
        id=AssessmentId("asm_r09"),
        case_id=CASE.id,
        gather_round=1,
        pack_version="1.0.0",
        eligible="eligible",
        facts=facts,
        input_hash=HASH,
    )


def intent(subject: str = "About your dispute", body: str = "", **numbers: str) -> ActionIntent:
    """`numbers` maps the text as written to the fact id it claims."""
    payload = MessagePayload(
        kind=MessageKind.EVIDENCE_REQUEST,
        to=SELLER,
        subject=subject,
        body=body,
        numbers_used=tuple(NumberUse(text=text, fact_id=fid) for text, fid in numbers.items()),
    )
    return ActionIntent(
        id=IntentId("int_r09"),
        case_id=CASE.id,
        source_kind=IntentSource.PROPOSAL,
        source_ref="prp_r09",
        action_type=ActionType.SEND_MESSAGE,
        payload=payload,
        payload_hash=HASH,
        status=IntentStatus.PLANNED,
        created_at=NOW,
    )


def verdict(message: ActionIntent, *facts: Fact, stage: PolicyStage = PolicyStage.PLANNING):
    ctx = PolicyContext(
        case=CASE,
        stage=stage,
        intent=message,
        assessment=assessment(*facts),
        policy=POLICY,
        now=NOW,
    )
    return MessageNumbersVerified().evaluate(ctx)


class TestAJustifiedMessagePasses:
    def test_a_cited_charge_is_allowed(self) -> None:
        message = intent(body="Your charge was ₹152.00.", **{"₹152.00": "f_charged"})
        assert verdict(message, CHARGED).verdict is VerdictKind.ALLOW

    def test_a_cited_weight_is_allowed(self) -> None:
        message = intent(body="We recorded 1,980 g.", **{"1,980 g": "f_grams"})
        assert verdict(message, GRAMS).verdict is VerdictKind.ALLOW

    def test_a_message_with_no_numbers_is_allowed(self) -> None:
        assert verdict(intent(body="Please send a photo.")).verdict is VerdictKind.ALLOW

    def test_a_non_message_intent_is_not_this_rule_s_business(self) -> None:
        ctx = PolicyContext(case=CASE, stage=PolicyStage.PLANNING, policy=POLICY, now=NOW)
        result = MessageNumbersVerified().evaluate(ctx)
        assert result.verdict is VerdictKind.ALLOW
        assert result.reason == "not_a_message"


class TestTheCatalogueNegatives:
    """policy-rules.md R09's own list, one test each."""

    def test_a_figure_with_no_fact_of_that_value_is_denied(self) -> None:
        """ "Rs 480" with no fact of that value - the seller's own claim, repeated back."""
        message = intent(body="You said the garment weighs Rs 480.")
        result = verdict(message, CHARGED)
        assert result.verdict is VerdictKind.DENY
        assert "numbers_unverified" in result.reason

    def test_a_correct_number_cited_with_the_wrong_fact_is_denied(self) -> None:
        """The one a looser check would miss. 1,980 is a real number on this case - it is
        the weight - and here it is written as the charge."""
        message = intent(body="Your charge was ₹1,980.00.", **{"₹1,980.00": "f_grams"})
        result = verdict(message, CHARGED, GRAMS)
        assert result.verdict is VerdictKind.DENY

    def test_a_fact_id_that_does_not_exist_is_denied(self) -> None:
        message = intent(body="Your charge was ₹152.00.", **{"₹152.00": "f_invented"})
        result = verdict(message, CHARGED)
        assert result.verdict is VerdictKind.DENY
        assert "f_invented" in result.reason

    def test_a_rounded_figure_is_denied(self) -> None:
        """ "1.98 kg" written as "2 kg". Rounding in prose is how a charge becomes wrong by
        a slab without anyone editing a number."""
        message = intent(body="We recorded 2 kg.", **{"2 kg": "f_grams"})
        assert verdict(message, GRAMS).verdict is VerdictKind.DENY

    def test_an_unjustified_number_in_the_subject_is_denied(self) -> None:
        """The subject is the first thing read and the easiest place to forget to check."""
        message = intent(subject="Refund of ₹500.00", body="")
        assert verdict(message, CHARGED).verdict is VerdictKind.DENY


class TestUnitsMustAgree:
    def test_a_weight_cannot_be_justified_by_a_money_fact(self) -> None:
        message = intent(body="We recorded 152 g.", **{"152 g": "f_charged"})
        result = verdict(message, CHARGED)
        assert result.verdict is VerdictKind.DENY
        assert "unit" in result.reason or "not" in result.reason

    def test_a_charge_cannot_be_justified_by_a_weight_fact(self) -> None:
        message = intent(body="Your charge was ₹1,980.00.", **{"₹1,980.00": "f_grams"})
        assert verdict(message, GRAMS).verdict is VerdictKind.DENY


class TestIdentifiersAreNotNumbers:
    """Rule 2. Without this the rule fails every message on its own case reference."""

    def test_the_shipment_reference_is_not_a_number_token(self) -> None:
        message = intent(body=f"About shipment {FACTS.shipment_ref}.")
        assert verdict(message).verdict is VerdictKind.ALLOW

    def test_the_claim_reference_is_not_a_number_token(self) -> None:
        message = intent(subject=f"{FACTS.claim_ref} update", body="")
        assert verdict(message).verdict is VerdictKind.ALLOW

    def test_all_three_references_together_are_fine(self) -> None:
        message = intent(
            body=f"{FACTS.shipment_ref} {FACTS.claim_ref} {FACTS.seller_ref}",
        )
        assert verdict(message).verdict is VerdictKind.ALLOW

    def test_masking_does_not_hide_a_real_number_beside_an_identifier(self) -> None:
        """The control. Masking removes the identifier and nothing else - a figure sitting
        next to it is still checked."""
        message = intent(body=f"{FACTS.shipment_ref} was charged ₹999.00.")
        assert verdict(message, CHARGED).verdict is VerdictKind.DENY


class TestItRunsAtBothStages:
    @pytest.mark.parametrize("stage", [PolicyStage.PLANNING, PolicyStage.EXECUTION])
    def test_an_unjustified_figure_is_denied_at_either_stage(self, stage: PolicyStage) -> None:
        """At planning it refuses to build the message; at execution it refuses to send it.
        The approval binding proves the text is the text that was approved, not that the
        numbers in it were ever true."""
        message = intent(body="We will refund ₹500.00.")
        assert verdict(message, CHARGED, stage=stage).verdict is VerdictKind.DENY


class TestWithNoAssessment:
    def test_any_figure_is_denied_when_there_are_no_facts_to_check_against(self) -> None:
        """Not allowed-by-default. A message with numbers and no assessment behind it is a
        message whose numbers came from somewhere this system cannot name."""
        ctx = PolicyContext(
            case=CASE,
            stage=PolicyStage.PLANNING,
            intent=intent(body="Your charge was ₹152.00.", **{"₹152.00": "f_charged"}),
            assessment=None,
            policy=POLICY,
            now=NOW,
        )
        assert MessageNumbersVerified().evaluate(ctx).verdict is VerdictKind.DENY


class TestTheReasonIsActionable:
    def test_it_names_the_offending_token(self) -> None:
        """ "numbers_unverified" alone tells a reviewer nothing about which figure to look
        at, and this denial is read by a person deciding what went wrong."""
        message = intent(body="We will refund ₹500.00.")
        assert "₹500.00" in verdict(message, CHARGED).reason

    def test_it_is_a_denial_not_an_escalation(self) -> None:
        """A message with an unexplained number is not a case needing judgement; it is a
        message that must not go out."""
        message = intent(body="We will refund ₹500.00.")
        assert verdict(message, CHARGED).verdict is VerdictKind.DENY
