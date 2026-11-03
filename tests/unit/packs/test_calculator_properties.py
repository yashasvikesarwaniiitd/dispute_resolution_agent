"""Card P2-10: properties of the calculator, over many boxes rather than a few.

Example tests check the arithmetic a reader can do by hand. These check the shape of the
function — the things that must hold for *every* parcel, which is where a rounding or
comparison bug actually lives. Each runs at least 200 examples.

**Monotonicity is the one that matters.** Growing a box must never lower its charge. It
sounds obvious and it is exactly what an off-by-one in a ceiling division breaks, in one
slab somewhere, for one seller, who then gets a bill that is less than their neighbour's for
a bigger parcel and writes in about it. Hypothesis finds that boundary; a hand-written
example sits next to it.

**Stability** is the I9 property: the same inputs give the same answer, and a value that has
already been rounded does not move when it is rounded again. Anything that drifts on a
second call is a float hiding somewhere.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from dispute_agent.packs.weight_dispute.calculator import (
    Dimensions,
    calculate,
    chargeable_grams,
    slabs_for,
    volumetric_grams,
)
from dispute_agent.packs.weight_dispute.rate_card import DEFAULT_RATE_CARD as CARD

MANY = settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow], deadline=None)

#: Millimetre precision, which is what a scanner reports and what `Dimensions` accepts.
#: Capped well above any real parcel: the properties are about shape, not about range.
cm = st.integers(min_value=1, max_value=3000).map(lambda mm: Decimal(mm) / Decimal(10))
grams = st.integers(min_value=0, max_value=200_000)
zones = st.sampled_from(sorted(zone.zone for zone in CARD.zones))


@st.composite
def boxes(draw: st.DrawFn) -> Dimensions:
    return Dimensions(length_cm=draw(cm), width_cm=draw(cm), height_cm=draw(cm))


class TestVolumetricWeight:
    @given(boxes())
    @MANY
    def test_it_is_never_negative(self, box: Dimensions) -> None:
        assert volumetric_grams(box, CARD) >= 0

    @given(boxes())
    @MANY
    def test_a_real_box_always_weighs_something(self, box: Dimensions) -> None:
        """Volume is positive, so the volumetric weight is at least one gram. A zero here
        would let a box be charged as though it took up no space."""
        assert volumetric_grams(box, CARD) >= 1

    @given(boxes(), st.integers(min_value=1, max_value=500))
    @MANY
    def test_growing_any_axis_never_lowers_it(self, box: Dimensions, extra_mm: int) -> None:
        extra = Decimal(extra_mm) / Decimal(10)
        base = volumetric_grams(box, CARD)
        for axis in ("length_cm", "width_cm", "height_cm"):
            bigger = Dimensions(**{**box.__dict__, axis: getattr(box, axis) + extra})
            assert volumetric_grams(bigger, CARD) >= base

    @given(boxes())
    @MANY
    def test_it_does_not_depend_on_which_axis_is_which(self, box: Dimensions) -> None:
        """A box on its side is the same box. If this ever failed, the divisor would be
        being applied to one axis rather than to the volume."""
        turned = Dimensions(length_cm=box.height_cm, width_cm=box.length_cm, height_cm=box.width_cm)
        assert volumetric_grams(box, CARD) == volumetric_grams(turned, CARD)


class TestChargeableWeight:
    @given(grams, grams)
    @MANY
    def test_it_is_never_less_than_either_input(self, dead: int, volumetric: int) -> None:
        """With the default card, which applies volumetric weight from zero grams up."""
        assert chargeable_grams(dead, volumetric, CARD) >= max(dead, volumetric)

    @given(grams, grams, st.integers(min_value=0, max_value=5000))
    @MANY
    def test_a_heavier_parcel_never_charges_less(
        self, dead: int, volumetric: int, extra: int
    ) -> None:
        base = chargeable_grams(dead, volumetric, CARD)
        assert chargeable_grams(dead + extra, volumetric, CARD) >= base
        assert chargeable_grams(dead, volumetric + extra, CARD) >= base


class TestSlabs:
    @given(grams)
    @MANY
    def test_there_is_always_at_least_one(self, g: int) -> None:
        """Nothing ships for nothing, and a zero-slab charge would make an empty bill look
        correct."""
        assert slabs_for(g, CARD) >= 1

    @given(grams, st.integers(min_value=0, max_value=5000))
    @MANY
    def test_more_grams_never_means_fewer_slabs(self, g: int, extra: int) -> None:
        assert slabs_for(g + extra, CARD) >= slabs_for(g, CARD)

    @given(grams)
    @MANY
    def test_a_slab_always_covers_its_grams(self, g: int) -> None:
        """The rounding is up, not to nearest: a parcel is never billed for less weight
        than it has."""
        assert slabs_for(g, CARD) * CARD.slab_grams >= g


class TestTheWholeCharge:
    @given(boxes(), grams, zones)
    @MANY
    def test_growing_the_box_never_lowers_the_bill(
        self, box: Dimensions, dead: int, zone: str
    ) -> None:
        """The property a seller would notice: a bigger parcel is never cheaper."""
        base = calculate(box, dead, zone, CARD).amount_paise
        bigger = Dimensions(
            length_cm=box.length_cm + Decimal("1"),
            width_cm=box.width_cm,
            height_cm=box.height_cm,
        )
        assert calculate(bigger, dead, zone, CARD).amount_paise >= base

    @given(boxes(), grams, zones)
    @MANY
    def test_a_heavier_parcel_never_costs_less(self, box: Dimensions, dead: int, zone: str) -> None:
        base = calculate(box, dead, zone, CARD).amount_paise
        assert calculate(box, dead + 1, zone, CARD).amount_paise >= base

    @given(boxes(), grams, zones)
    @MANY
    def test_the_bill_is_never_zero(self, box: Dimensions, dead: int, zone: str) -> None:
        assert calculate(box, dead, zone, CARD).amount_paise > 0

    @given(boxes(), grams, zones)
    @MANY
    def test_the_amount_is_the_slabs_times_the_rate(
        self, box: Dimensions, dead: int, zone: str
    ) -> None:
        """No tariff arithmetic anywhere but here. If the amount ever stopped being exactly
        slabs x rate, some other rule would have crept into the money path."""
        charge = calculate(box, dead, zone, CARD)
        assert charge.amount_paise == charge.slabs * charge.rate_paise_per_slab

    @given(boxes(), grams, zones, zones)
    @MANY
    def test_a_dearer_zone_never_costs_less(
        self, box: Dimensions, dead: int, cheap: str, dear: str
    ) -> None:
        assume(CARD.rate_paise_for(cheap) <= CARD.rate_paise_for(dear))
        assert (
            calculate(box, dead, dear, CARD).amount_paise
            >= calculate(box, dead, cheap, CARD).amount_paise
        )


class TestStability:
    """I9: the same inputs give the same answer, every time and on any run."""

    @given(boxes(), grams, zones)
    @MANY
    def test_calculating_twice_gives_the_same_charge(
        self, box: Dimensions, dead: int, zone: str
    ) -> None:
        assert calculate(box, dead, zone, CARD) == calculate(box, dead, zone, CARD)

    @given(grams)
    @MANY
    def test_rounding_an_already_rounded_weight_changes_nothing(self, g: int) -> None:
        """A charge recomputed from its own chargeable weight lands on the same slab. If
        this drifted, a credit computed from a stored assessment would not match the bill
        it was supposed to correct."""
        once = slabs_for(g, CARD)
        rounded = once * CARD.slab_grams
        assert slabs_for(rounded, CARD) == once

    @given(boxes())
    @MANY
    def test_the_volumetric_weight_of_a_rounded_box_is_stable(self, box: Dimensions) -> None:
        assert volumetric_grams(box, CARD) == volumetric_grams(box, CARD)

    @given(boxes(), grams, zones)
    @MANY
    def test_no_float_reaches_the_answer(self, box: Dimensions, dead: int, zone: str) -> None:
        """Every figure out of the calculator is an integer of paise or grams. A float here
        is a rupee that disagrees with itself depending on when it was computed."""
        charge = calculate(box, dead, zone, CARD)
        for value in (
            charge.amount_paise,
            charge.slabs,
            charge.chargeable_grams,
            charge.volumetric_grams,
            charge.dead_grams,
            charge.rate_paise_per_slab,
        ):
            assert isinstance(value, int)
            assert not isinstance(value, bool)
