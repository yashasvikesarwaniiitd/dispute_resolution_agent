"""The credit card's "What you are approving" line (design v2): code's own terms, and only
when they give exactly the amount the approval is bound to."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.ui.review import equation as _equation
from dispute_agent.ui.review_view import _equation_html
from tests.unit.policy.test_credit_and_signal_rules import assessment


def card(amount: str, reason: str) -> Any:
    return SimpleNamespace(
        item=SimpleNamespace(credit_amount=render_fact(Money.of(amount)), credit_reason=reason)
    )


def rupees(value: str) -> str:
    return render_fact(Money.of(value))


def test_a_calculation_error_reads_billed_less_correct_less_already_credited() -> None:
    latest = assessment(charged="190.00", recalculated="38.00")
    terms = _equation(latest, card("152.00", "calculation_error"), Decimal(0), Decimal(0))
    assert terms == [
        ("Billed", rupees("190.00")),
        ("Correct charge", rupees("38.00")),
        ("Already credited", rupees("0.00")),
    ]


def test_an_earlier_duplicate_refund_is_not_netted_from_an_overcharge() -> None:
    latest = assessment(charged="190.00", recalculated="38.00")
    terms = _equation(latest, card("152.00", "calculation_error"), Decimal(76), Decimal(76))
    assert terms is not None and terms[2] == ("Already credited", rupees("0.00"))


def test_no_line_when_the_terms_do_not_give_the_amount_under_approval() -> None:
    latest = assessment(charged="190.00", recalculated="38.00")
    assert _equation(latest, card("150.00", "calculation_error"), Decimal(0), Decimal(0)) is None
    assert _equation(None, card("152.00", "calculation_error"), Decimal(0), Decimal(0)) is None
    assert _equation(latest, card("152.00", "remeasurement_lower"), Decimal(0), Decimal(0)) is None


def test_a_duplicate_charge_reads_duplicate_debit_less_earlier_duplicate_refunds() -> None:
    latest = assessment(
        charged="152.00",
        recalculated="152.00",
        errors=("duplicate_charge",),
        duplicate_total="152.00",
    )
    terms = _equation(latest, card("152.00", "duplicate_charge"), Decimal(0), Decimal(0))
    assert terms == [
        ("Duplicate debit", rupees("152.00")),
        ("Already refunded for duplicates", rupees("0.00")),
    ]


def test_the_line_is_escaped_text() -> None:
    html = _equation_html([("<b>Billed</b>", "₹1.00")], "Credit · x", "₹1.00")
    assert "<b>" not in html and "&lt;b&gt;Billed" in html
    assert 'aria-label="What you are approving"' in html
