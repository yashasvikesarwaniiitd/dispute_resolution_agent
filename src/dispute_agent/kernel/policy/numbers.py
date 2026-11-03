"""Number validation for outbound messages (policy-rules.md section 7, R09, I9).

Every number a seller reads must be one the system computed and stored. Not one it believes,
not one a model wrote, not one a template interpolated from somewhere helpful — a number
that traces to a `Fact` with an id.

This module does two jobs that have to agree exactly:

`render_fact` turns a stored value into the text a message may contain. It is the **only**
place a number becomes words, so the shape of "₹152.00" is decided once.

`extract_numbers` finds every number token in a finished message, and `justify` checks each
one against the facts it claims. A token nobody claimed fails, and a token claimed against
the wrong fact fails, and both of those are how a wrong figure reaches a seller.

**What this deliberately does not catch:** numbers written as words. "four hundred rupees"
is invisible to a tokeniser built on digits. That is residual risk RR1, recorded rather than
pretended away, and the mitigation is twofold — every message is reviewed by a person in the
pilot (D6), and the drafter and templates are both required to write digits only (MD-F1), so
a number word in a draft is itself the anomaly.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, NamedTuple

from dispute_agent.kernel.domain.values import Money, Weight

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from dispute_agent.kernel.domain.evidence import Fact
    from dispute_agent.kernel.domain.reasoning import NumberUse

#: policy-rules.md section 7, verbatim. Currency prefix, digits with optional grouping and
#: decimals, optional unit.
TOKEN = re.compile(
    r"(?:₹|Rs\.?|INR)?\s?\d[\d,]*(?:\.\d+)?\s?(?:kg|g|cm|%)?",
    re.IGNORECASE,
)

#: Units a fact can carry. A token's unit must agree with its fact's: "1980 g" justified by
#: a money fact of 1980 would otherwise pass, and that is a bill read as a weight.
GRAMS = "g"
KILOGRAMS = "kg"


class NumberToken(NamedTuple):
    """One number as it appears in the text."""

    text: str
    start: int
    end: int

    @property
    def unit(self) -> str | None:
        match = re.search(r"(kg|g|cm|%)\s*$", self.text, re.IGNORECASE)
        return match.group(1).lower() if match else None

    @property
    def amount(self) -> Decimal | None:
        """The numeric part, or None when it will not parse."""
        digits = re.sub(r"[^\d.]", "", self.text)
        try:
            return Decimal(digits) if digits else None
        except InvalidOperation:
            return None


def render_fact(value: object) -> str:
    """The one place a stored value becomes text a message may carry.

    Money carries its symbol and two decimal places, weight is written in grams, and a
    Decimal is rendered exactly as stored. Having one renderer is what makes the check
    possible at all: if a template formatted money its own way, a correct number could fail
    validation and an incorrect one could pass.
    """
    if isinstance(value, Money):
        return f"₹{_group(value.amount, places=2)}"
    if isinstance(value, Weight):
        return f"{_group(Decimal(value.to_grams()), places=0)} {GRAMS}"
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bool):  # before int: bool is an int, and "True" is not a number
        return str(value)
    if isinstance(value, int):
        return _group(Decimal(value), places=0)
    return str(value)


def extract_numbers(text: str, *, masks: Iterable[str] = ()) -> tuple[NumberToken, ...]:
    """Every number token, with identifiers and rendered dates masked out first.

    Masking matters more than it looks. `CLM-000001` contains `000001`, and `5 November
    2026` contains `5` and `2026`. Tokenising first and explaining afterwards would mean
    every message failed on the parts of its own case reference, so whole strings the system
    itself rendered are removed before the tokeniser ever sees them (rules 2 and 3).
    """
    masked = text
    # Longest first, so a mask contained in another cannot blank out half of it.
    for mask in sorted(masks, key=len, reverse=True):
        if mask:
            masked = masked.replace(mask, " " * len(mask))

    tokens = []
    for match in TOKEN.finditer(masked):
        token = match.group().strip()
        if token and re.search(r"\d", token):
            tokens.append(NumberToken(token, match.start(), match.end()))
    return tuple(tokens)


def justify(
    token: NumberToken,
    claims: Sequence[NumberUse],
    facts: Mapping[str, Fact],
) -> str | None:
    """Why this token is not justified, or None when it is.

    Returning the reason rather than a bare bool is deliberate: "no fact claims this number"
    and "the claimed fact says something else" are different failures, and a policy denial
    that cannot say which is a denial nobody can act on.
    """
    claimed = [use for use in claims if use.text.strip() == token.text]
    if not claimed:
        return f"no fact is cited for {token.text!r}"

    for use in claimed:
        fact = facts.get(use.fact_id)
        if fact is None:
            return f"{token.text!r} cites fact {use.fact_id!r}, which does not exist"
        mismatch = _matches(token, fact)
        if mismatch is None:
            return None
    return mismatch


def _matches(token: NumberToken, fact: Fact) -> str | None:
    """None when the token really says what the fact holds."""
    rendered = render_fact(fact.value)
    if token.text == rendered:
        return None

    expected_unit = _unit_of(fact.value)
    if token.unit != expected_unit:
        return (
            f"{token.text!r} is in {token.unit or 'no unit'} but fact {fact.fact_id!r} "
            f"is in {expected_unit or 'no unit'}"
        )

    amount = token.amount
    value = _numeric(fact.value)
    if amount is None or value is None:
        return f"{token.text!r} does not match fact {fact.fact_id!r} ({rendered})"
    if amount != value:
        # Numeric equality, so "₹1,234.50" and "INR 1234.5" agree while "1234" does not.
        return f"{token.text!r} is not {rendered} (fact {fact.fact_id!r})"
    return None


def _unit_of(value: object) -> str | None:
    if isinstance(value, Weight):
        return GRAMS
    return None


def _numeric(value: object) -> Decimal | None:
    if isinstance(value, Money):
        return value.amount
    if isinstance(value, Weight):
        return Decimal(value.to_grams())
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    return None


def _group(value: Decimal, *, places: int) -> str:
    quantised = value.quantize(Decimal(1).scaleb(-places)) if places else value.to_integral_value()
    return f"{quantised:,.{places}f}"


__all__ = [
    "GRAMS",
    "TOKEN",
    "NumberToken",
    "extract_numbers",
    "justify",
    "render_fact",
]
