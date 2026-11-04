"""Card P3-05: a rendered message carries no number that is not a stored fact.

Two tests carry the card, and they are opposites.

`test_every_number_in_the_body_is_justified` renders a real message and runs R09's own
tokeniser over it, checking each token against the citations the render produced. This is
the one that would catch a template quietly formatting a figure itself.

`test_a_missing_fact_refuses_to_render` is the other direction. A template that leaves a
blank where a number should be does not look broken to a template engine — "your charge of
 was calculated from" renders perfectly well — and it is what a seller would receive.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from dispute_agent.kernel.domain.enums import MessageKind
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.evidence import Fact
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.reasoning import DraftContext
from dispute_agent.kernel.domain.values import Money, Weight
from dispute_agent.kernel.policy.numbers import extract_numbers, justify, render_fact
from dispute_agent.packs.weight_dispute.render import (
    TEMPLATES,
    render,
    template_names,
)

CASE = CaseId("case_templates")
CASE_REF = "CASE-1042"
SHIPMENT = "SHP-000001"
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)

FACTS = (
    Fact(fact_id="f_charged", label="amount charged", value=Money.of("152.00")),
    Fact(
        fact_id="f_chargeable_grams",
        label="chargeable weight in grams",
        value=Weight.from_grams(1980),
    ),
)

ITEMS = (
    "Photo of the packed box beside a tape measure",
    "Packaging dimensions at dispatch",
)


def a_context(
    kind: MessageKind = MessageKind.EVIDENCE_REQUEST,
    facts: tuple[Fact, ...] = FACTS,
) -> DraftContext:
    return DraftContext(
        case_id=CASE,
        case_ref=CASE_REF,
        kind=kind,
        facts=facts,
        seller_ref="SELL-0001",
        requested_items=ITEMS,
        # Deliberately carries no figure. `next_step` is prose the caller supplies and
        # the template interpolates verbatim, so a number in it would be a number with
        # no fact id behind it - R09 would deny the message at send time. Tested below.
        next_step="We will decide as soon as your reply arrives.",
    )


def rendered(**kwargs: object) -> object:
    return render(a_context(**kwargs), shipment_ref=SHIPMENT)  # type: ignore[arg-type]


class TestItRenders:
    def test_an_evidence_request_has_a_subject_and_a_body(self) -> None:
        message = render(a_context(), shipment_ref=SHIPMENT)
        assert message.subject
        assert "Subject:" not in message.body
        assert message.template_id == "evidence_request.md.j2"

    def test_the_requested_items_appear(self) -> None:
        body = render(a_context(), shipment_ref=SHIPMENT).body
        for item in ITEMS:
            assert item in body

    @pytest.mark.parametrize("kind", [MessageKind.EVIDENCE_REQUEST, MessageKind.REMINDER])
    def test_each_requested_item_is_on_its_own_line(self, kind: MessageKind) -> None:
        """The first live Foundry run showed three items run together on one line - the
        loop stripped the line break before each item. "Appears in the body" passed."""
        lines = render(a_context(kind=kind), shipment_ref=SHIPMENT).body.splitlines()
        for item in ITEMS:
            assert f"- {item}" in lines, f"{item!r} is not on a line of its own"

    def test_the_case_and_shipment_are_named(self) -> None:
        message = render(a_context(), shipment_ref=SHIPMENT)
        assert CASE_REF in message.subject
        assert SHIPMENT in message.body

    def test_a_reminder_renders_too(self) -> None:
        message = render(a_context(MessageKind.REMINDER), shipment_ref=SHIPMENT)
        assert message.template_id == "reminder.md.j2"
        assert SHIPMENT in message.body

    def test_rendering_is_deterministic(self) -> None:
        """The approved text and the sent text are compared by hash (I13). A render that
        varied between calls would fail that comparison for no reason anyone could see."""
        assert render(a_context(), shipment_ref=SHIPMENT) == render(
            a_context(), shipment_ref=SHIPMENT
        )


class TestEveryNumberIsAFact:
    """I9 and R09, checked with R09's own tokeniser rather than by eye."""

    def test_every_number_in_the_body_is_justified(self) -> None:
        message = render(a_context(), shipment_ref=SHIPMENT)
        facts = {f.fact_id: f for f in FACTS}
        masks = (CASE_REF, SHIPMENT)

        for token in extract_numbers(message.body, masks=masks):
            problem = justify(token, message.numbers_used, facts)
            assert problem is None, f"{token.text!r} in the body: {problem}"

    def test_every_number_in_the_subject_is_justified(self) -> None:
        message = render(a_context(), shipment_ref=SHIPMENT)
        facts = {f.fact_id: f for f in FACTS}
        for token in extract_numbers(message.subject, masks=(CASE_REF, SHIPMENT)):
            assert justify(token, message.numbers_used, facts) is None

    def test_the_citations_name_facts_this_case_actually_has(self) -> None:
        message = render(a_context(), shipment_ref=SHIPMENT)
        known = {f.fact_id for f in FACTS}
        assert message.numbers_used
        assert {use.fact_id for use in message.numbers_used} <= known

    def test_the_cited_text_is_what_the_renderer_produces(self) -> None:
        """So the citation matches the characters in the message, not an approximation."""
        message = render(a_context(), shipment_ref=SHIPMENT)
        facts = {f.fact_id: f for f in FACTS}
        for use in message.numbers_used:
            assert use.text == render_fact(facts[use.fact_id].value)
            assert use.text in message.body

    def test_a_figure_smuggled_in_through_next_step_is_not_justified(self) -> None:
        """`next_step` is prose the caller supplies and the template interpolates verbatim,
        which makes it the one route a number can enter a message without a fact behind it.

        The template cannot judge it - a legitimate `next_step` may cite a template-constant
        fact - so the gate is R09 at send time, and this is the test that the gate is real.
        A message like this one is denied rather than sent.
        """
        ctx = a_context().model_copy(
            update={"next_step": "We will decide within 3 days of your reply."}
        )
        message = render(ctx, shipment_ref=SHIPMENT)
        facts = {f.fact_id: f for f in FACTS}

        unjustified = [
            token
            for token in extract_numbers(message.body, masks=(CASE_REF, SHIPMENT))
            if justify(token, message.numbers_used, facts) is not None
        ]
        assert [t.text for t in unjustified] == ["3"]

    def test_an_uncited_number_would_be_caught(self) -> None:
        """The control. If `justify` passed everything, every test above would be empty."""
        facts = {f.fact_id: f for f in FACTS}
        message = render(a_context(), shipment_ref=SHIPMENT)
        smuggled = extract_numbers("we will refund ₹999.00", masks=())
        assert justify(smuggled[0], message.numbers_used, facts) is not None


class TestItRefusesRatherThanLeavingABlank:
    def test_a_missing_fact_refuses_to_render(self) -> None:
        """A blank where a number goes renders perfectly well and reaches the seller."""
        with pytest.raises(DomainValidationError, match="f_charged"):
            render(a_context(facts=FACTS[1:]), shipment_ref=SHIPMENT)

    def test_no_facts_at_all_refuses(self) -> None:
        with pytest.raises(DomainValidationError):
            render(a_context(facts=()), shipment_ref=SHIPMENT)

    def test_a_kind_with_no_template_refuses(self) -> None:
        """Upheld-charge arrives in Phase 5. Falling back to something generic would be a
        generic message about money, which is worse than no message."""
        with pytest.raises(DomainValidationError, match="no template"):
            render(a_context(MessageKind.UPHELD_EXPLANATION), shipment_ref=SHIPMENT)

    def test_the_error_names_what_was_missing(self) -> None:
        """So the failure is actionable rather than "a template broke"."""
        with pytest.raises(DomainValidationError) as refused:
            render(a_context(facts=FACTS[1:]), shipment_ref=SHIPMENT)
        assert "f_charged" in str(refused.value)


class TestDigitsOnly:
    """MD-F1. R09's tokeniser cannot see number words, so they must not be written."""

    def test_no_rendered_message_contains_a_number_word(self) -> None:
        for kind in (MessageKind.EVIDENCE_REQUEST, MessageKind.REMINDER):
            message = render(a_context(kind), shipment_ref=SHIPMENT)
            from dispute_agent.packs.weight_dispute.render import NUMBER_WORDS

            assert NUMBER_WORDS.search(message.body) is None, kind
            assert NUMBER_WORDS.search(message.subject) is None, kind

    def test_a_template_that_spelled_a_number_out_would_be_refused(self) -> None:
        """The control for the test above: the check is real, not a no-op on this wording."""
        from dispute_agent.packs.weight_dispute.render import NUMBER_WORDS

        assert NUMBER_WORDS.search("we credited four hundred rupees") is not None


class TestTheTemplateFilesAndTheRegistryAgree:
    def test_every_registered_template_exists_on_disk(self) -> None:
        for name in template_names():
            assert (TEMPLATES / name).is_file(), f"{name} is registered but missing"

    def test_every_template_on_disk_is_registered(self) -> None:
        """An unregistered template is wording nobody can send and nobody reviews."""
        on_disk = {p.name for p in Path(TEMPLATES).glob("*.j2")}
        assert on_disk == set(template_names())

    def test_every_template_starts_with_a_subject_line(self) -> None:
        for name in template_names():
            first = (TEMPLATES / name).read_text(encoding="utf-8").splitlines()[0]
            assert first.lower().startswith("subject:"), name

    def test_no_template_hard_codes_a_figure(self) -> None:
        """Every number must arrive through `fact(...)`. A literal in a template is a
        number with no id, which R09 would deny at send time - better to never write it."""
        import re

        for name in template_names():
            text = (TEMPLATES / name).read_text(encoding="utf-8")
            # Strip the Jinja expressions, then look for bare digits in the prose.
            prose = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", text, flags=re.DOTALL)
            assert not re.search(r"\d", prose), f"{name} contains a literal digit"


class TestRenderFact:
    """The single renderer both the templates and policy use."""

    def test_money_carries_its_symbol_and_two_decimals(self) -> None:
        assert render_fact(Money.of("152.00")) == "₹152.00"

    def test_large_money_is_grouped(self) -> None:
        assert render_fact(Money.of("1234.50")) == "₹1,234.50"

    def test_weight_is_written_in_grams(self) -> None:
        assert render_fact(Weight.from_grams(1980)) == "1,980 g"

    def test_a_decimal_is_exact(self) -> None:
        assert render_fact(Decimal("22.5")) == "22.5"

    def test_an_integer_constant_renders_plainly(self) -> None:
        assert render_fact(72) == "72"

    def test_a_weight_token_is_not_justified_by_a_money_fact(self) -> None:
        """Units must agree. Otherwise "1,980 g" could be justified by a bill of 1980,
        which is a charge read back to the seller as a weight."""
        fact = Fact(fact_id="f_money", label="charged", value=Money.of("1980.00"))
        token = extract_numbers("1,980 g")[0]
        assert justify(token, (), {"f_money": fact}) is not None
