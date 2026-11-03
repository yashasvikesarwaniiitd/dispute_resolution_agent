"""The calculator (money-rules.md 1-3 and 7, ADR 0011, D10, I9).

Every number the system ever puts in front of a seller comes from here, which is why this
file is the one with the strictest rules in the project.

**Integers all the way down.** Weight is grams, money is paise, and the only division is the
volumetric one — which rounds up, once, to the gram. `rate_paise_per_slab * slabs` is an
integer multiplication, so in the normal path there is no rounding at all. A float never
enters: `Decimal` for dimensions, `int` for everything derived.

**Rounding happens once, where the rule says.** Two places, both deliberate:

- volumetric grams = ceil(L x W x H x 1000 / divisor) — up, to the gram
- slabs = ceil(chargeable_grams / slab_grams) — up, to the slab, which is the published
  convention this pack models

Rounding per slab instead of once is what `calc_error_high_value` exists to catch: 7,000 g at
zone E is 14 slabs at INR 70.00, exactly INR 980.00. Round in the wrong place and the credit
comes out wrong by rupees, on a case where a seller is already unhappy.

**No figure lives here.** Divisor, slab size, rates and thresholds all come from the rate
card (D2, D10). An architecture test asserts the word `float` never appears in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money, Weight
from dispute_agent.packs.weight_dispute.rate_card import decimal_cm

if TYPE_CHECKING:
    from dispute_agent.packs.weight_dispute.rate_card import RateCard

#: A cubic centimetre is a thousandth of a litre; the divisor convention is expressed per
#: 1000 cm³, so the numerator carries the factor rather than the divisor (money-rules 7).
GRAMS_PER_CM3_NUMERATOR = 1000


@dataclass(frozen=True)
class Dimensions:
    """Centimetres, to the millimetre. Never floats (I9)."""

    length_cm: Decimal
    width_cm: Decimal
    height_cm: Decimal

    @classmethod
    def of(cls, length: object, width: object, height: object) -> Dimensions:
        return cls(
            length_cm=decimal_cm(length, "length_cm"),
            width_cm=decimal_cm(width, "width_cm"),
            height_cm=decimal_cm(height, "height_cm"),
        )

    @property
    def volume_cm3(self) -> Decimal:
        return self.length_cm * self.width_cm * self.height_cm


@dataclass(frozen=True)
class Charge:
    """One calculated charge, with every step kept so a message can cite them (I9).

    The intermediate values are not decoration. R09 refuses a message whose numbers do not
    trace to an assessment fact, so "4 slabs" and "1,980 g" have to be things the system
    computed and kept, not things a sentence asserted.
    """

    dead_grams: int
    volumetric_grams: int
    chargeable_grams: int
    slabs: int
    rate_paise_per_slab: int
    zone: str
    amount_paise: int

    @property
    def amount(self) -> Money:
        return Money.from_paise(self.amount_paise)

    @property
    def chargeable(self) -> Weight:
        return Weight.from_grams(self.chargeable_grams)

    @property
    def volumetric(self) -> Weight:
        return Weight.from_grams(self.volumetric_grams)


def volumetric_grams(dimensions: Dimensions, card: RateCard) -> int:
    """ceil(L x W x H x 1000 / divisor), to the gram.

    Integer arithmetic on the Decimal volume: the numerator is exact, so the only rounding
    is the final ceiling, applied once.
    """
    numerator = dimensions.volume_cm3 * GRAMS_PER_CM3_NUMERATOR
    return _ceil_div(_exact_int(numerator, "volume"), card.volumetric_divisor)


def chargeable_grams(
    dead_grams: int, volumetric: int, card: RateCard, packaging: str | None = None
) -> int:
    """The greater of dead and volumetric, with the configured exceptions.

    Below `volumetric_applies_above_grams` (default 0, so normally never) and for packaging
    types the card exempts, the dead weight stands alone — a flyer bag of documents should
    not be charged as though it were a box of air.
    """
    _check_grams(dead_grams, "dead_grams")
    _check_grams(volumetric, "volumetric_grams")

    if packaging is not None and packaging in card.volumetric_exempt_packaging:
        return dead_grams
    if dead_grams < card.volumetric_applies_above_grams:
        return dead_grams
    return max(dead_grams, volumetric)


def slabs_for(grams: int, card: RateCard) -> int:
    """ceil(grams / slab_grams). Rounding up is the rule, not the arithmetic's accident.

    A zero-gram shipment still occupies one slab: nothing ships for nothing, and a zero-slab
    charge would make an empty bill look correct.
    """
    _check_grams(grams, "chargeable_grams")
    return max(1, _ceil_div(grams, card.slab_grams))


def calculate(
    dimensions: Dimensions,
    dead_grams: int,
    zone: str,
    card: RateCard,
    packaging: str | None = None,
) -> Charge:
    """The whole calculation, with every intermediate kept.

    Deterministic by construction: the same inputs and the same card always give the same
    Charge, which is what lets `assess` promise the same (I9).
    """
    volumetric = volumetric_grams(dimensions, card)
    chargeable = chargeable_grams(dead_grams, volumetric, card, packaging)
    slabs = slabs_for(chargeable, card)
    rate_paise = card.rate_paise_for(zone)
    return Charge(
        dead_grams=dead_grams,
        volumetric_grams=volumetric,
        chargeable_grams=chargeable,
        slabs=slabs,
        rate_paise_per_slab=rate_paise,
        zone=zone,
        # One integer multiplication. No rounding here at all, which is the whole point of
        # holding rates in paise (money-rules.md section 7).
        amount_paise=rate_paise * slabs,
    )


def difference(charged: Money, recalculated: Money) -> Money:
    """How much was overcharged. Never negative — an undercharge is not this system's business."""
    if recalculated > charged:
        return Money.of("0.00")
    return charged - recalculated


def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division. No float, no `math.ceil`, no precision to lose."""
    if denominator <= 0:
        raise DomainValidationError(f"cannot divide by {denominator}")
    return -(-numerator // denominator)


def _exact_int(value: Decimal, what: str) -> int:
    """A Decimal that must already be a whole number of the unit, rounded up to it.

    The volume of a box measured to the millimetre can have decimal places; rounding up here
    is the same ceiling the formula calls for, done before the division so the division stays
    integer.
    """
    if not value.is_finite() or value <= 0:
        raise DomainValidationError(f"{what} must be a positive quantity: {value}")
    whole = int(value)
    return whole if Decimal(whole) == value else whole + 1


def _check_grams(value: int, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(f"{what} must be an integer number of grams: {value!r}")
    if value < 0:
        raise DomainValidationError(f"{what} must not be negative: {value}")
