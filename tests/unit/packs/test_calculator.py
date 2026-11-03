"""Card P2-02: the calculator, against the worked examples in the specs.

Two fixtures carry the weight here. The **kurta** case is the one in `FLOW.md` section 7a,
written out so a reader can follow it: it must reproduce exactly, because it is the example
the whole project explains itself with.

`calc_error_high_value` is the one that catches sloppiness. 7,000 g at zone E is 14 slabs at
INR 70.00 — exactly INR 980.00 — against a bill of 40 slabs, INR 2,800.00, for a credit of
INR 1,820.00. Round per slab instead of once and this comes out wrong by rupees, on a case
where a seller is already unhappy.
"""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest

from dispute_agent.kernel.domain.errors import ConfigError, DomainValidationError
from dispute_agent.kernel.domain.values import Money, Weight
from dispute_agent.packs.weight_dispute.calculator import (
    GRAMS_PER_CM3_NUMERATOR,
    Dimensions,
    calculate,
    chargeable_grams,
    difference,
    slabs_for,
    volumetric_grams,
)
from dispute_agent.packs.weight_dispute.rate_card import (
    DEFAULT_RATE_CARD,
    RateCard,
    ZoneRate,
    load_rate_card,
)

CARD = DEFAULT_RATE_CARD


class TestTheKurtaFixture:
    """synthetic-data.md section 4 and FLOW.md section 7a, number for number."""

    declared = Dimensions.of("20", "15", "10")
    measured = Dimensions.of("30", "22", "15")

    def test_declared_volumetric_is_600_grams(self) -> None:
        assert volumetric_grams(self.declared, CARD) == 600

    def test_measured_volumetric_is_1980_grams(self) -> None:
        assert volumetric_grams(self.measured, CARD) == 1980

    def test_chargeable_is_the_volumetric_weight(self) -> None:
        """Dead 1,100 g against volumetric 1,980 g: the box wins, which is the whole dispute."""
        assert chargeable_grams(1100, 1980, CARD) == 1980

    def test_it_is_four_slabs(self) -> None:
        assert slabs_for(1980, CARD) == 4

    def test_the_charge_is_152_rupees(self) -> None:
        charge = calculate(self.measured, dead_grams=1100, zone="B", card=CARD)
        assert charge.amount == Money.of("152.00")
        assert charge.slabs == 4
        assert charge.chargeable == Weight.from_grams(1980)

    def test_every_step_is_kept_for_a_message_to_cite(self) -> None:
        """R09 refuses a message whose numbers do not trace to a fact (I9)."""
        charge = calculate(self.measured, dead_grams=1100, zone="B", card=CARD)
        assert charge.dead_grams == 1100
        assert charge.volumetric_grams == 1980
        assert charge.chargeable_grams == 1980
        assert charge.rate_paise_per_slab == 3800
        assert charge.zone == "B"


class TestCalcErrorWrongSlab:
    """Billed for five slabs instead of four: a credit of exactly one slab."""

    def test_the_overcharge_is_one_slab(self) -> None:
        correct = calculate(Dimensions.of("30", "22", "15"), 1100, "B", CARD)
        billed = Money.of("190.00")  # 5 slabs
        assert difference(billed, correct.amount) == Money.of("38.00")


class TestCalcErrorHighValue:
    """The arithmetic that catches rounding applied in the wrong place."""

    def test_true_charge_is_980_rupees(self) -> None:
        assert slabs_for(7000, CARD) == 14
        assert CARD.rate_for("E") == Money.of("70.00")
        assert Money.from_paise(CARD.rate_paise_for("E") * 14) == Money.of("980.00")

    def test_billed_charge_is_2800_rupees(self) -> None:
        assert slabs_for(20000, CARD) == 40
        assert Money.from_paise(CARD.rate_paise_for("E") * 40) == Money.of("2800.00")

    def test_the_credit_is_exactly_26_slabs(self) -> None:
        true_charge = Money.from_paise(CARD.rate_paise_for("E") * slabs_for(7000, CARD))
        billed = Money.from_paise(CARD.rate_paise_for("E") * slabs_for(20000, CARD))
        credit = difference(billed, true_charge)
        assert credit == Money.of("1820.00")
        assert credit.to_paise() == 26 * CARD.rate_paise_for("E")


class TestRoundingHappensOnceAndUpwards:
    def test_a_gram_over_a_slab_is_a_whole_new_slab(self) -> None:
        """Rounding up is the published convention the pack models, so it is a rule."""
        assert slabs_for(500, CARD) == 1
        assert slabs_for(501, CARD) == 2

    def test_volumetric_rounds_up_to_the_gram(self) -> None:
        """10.1 x 10 x 10 cm is 1010 cm3 -> 1,010,000 / 5000 = 202 g exactly."""
        assert volumetric_grams(Dimensions.of("10.1", "10", "10"), CARD) == 202

    def test_a_fractional_gram_rounds_up_not_down(self) -> None:
        """1 x 1 x 1 cm is 1000/5000 = 0.2 g, which must become 1 g, not 0."""
        assert volumetric_grams(Dimensions.of("1", "1", "1"), CARD) == 1

    def test_nothing_ships_for_nothing(self) -> None:
        """A zero-slab charge would make an empty bill look correct."""
        assert slabs_for(0, CARD) == 1

    def test_the_charge_is_an_exact_multiple_of_the_rate(self) -> None:
        """No rounding exists in the normal path, so this holds for every slab count."""
        for grams in (1, 500, 501, 7000, 20000, 123456):
            charge = calculate(Dimensions.of("1", "1", "1"), grams, "E", CARD)
            assert charge.amount_paise % CARD.rate_paise_for("E") == 0


class TestChargeableWeight:
    def test_the_greater_of_dead_and_volumetric_wins(self) -> None:
        assert chargeable_grams(2000, 1980, CARD) == 2000
        assert chargeable_grams(1100, 1980, CARD) == 1980

    def test_a_threshold_can_exempt_light_parcels(self) -> None:
        """A flyer of documents should not be charged as a box of air."""
        card = CARD.model_copy(update={"volumetric_applies_above_grams": 1500})
        assert chargeable_grams(1100, 1980, card) == 1100
        assert chargeable_grams(1600, 1980, card) == 1980

    def test_packaging_can_exempt_it_too(self) -> None:
        card = CARD.model_copy(update={"volumetric_exempt_packaging": frozenset({"flyer"})})
        assert chargeable_grams(1100, 1980, card, packaging="flyer") == 1100
        assert chargeable_grams(1100, 1980, card, packaging="box") == 1980


class TestNoFloatsReachTheArithmetic:
    """I9. A float cannot represent 0.1 exactly, and a rupee lost here is a rupee a seller
    writes in about."""

    def test_a_float_dimension_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="float"):
            Dimensions.of(20.0, 15, 10)

    def test_a_float_dead_weight_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="integer number of grams"):
            chargeable_grams(1100.0, 1980, CARD)  # type: ignore[arg-type]

    def test_a_dimension_finer_than_a_millimetre_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="millimetre"):
            Dimensions.of("20.05", "15", "10")

    def test_a_negative_dimension_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="positive length"):
            Dimensions.of("-20", "15", "10")

    def test_no_float_appears_in_the_calculator(self) -> None:
        """Card P2-02, as an AST walk rather than a grep.

        The first version of this searched the source text and flagged its own docstrings,
        which is exactly the kind of test that gets weakened until it passes. This reads the
        code: no float literal, and no use of `float` outside the one isinstance guard that
        exists to reject them.
        """
        tree = _calculator_ast()
        literals = [
            n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)
        ]
        assert not literals, [n.value for n in literals]

        uses = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Name) and n.id == "float" and not _inside_isinstance(tree, n)
        ]
        assert not uses, "float is used outside the guard that rejects it"


class TestDeterminism:
    def test_the_same_inputs_always_give_the_same_charge(self) -> None:
        """I9: equal bundles give equal assessments, which starts here."""
        first = calculate(Dimensions.of("30", "22", "15"), 1100, "B", CARD)
        second = calculate(Dimensions.of("30", "22", "15"), 1100, "B", CARD)
        assert first == second

    def test_an_undercharge_is_not_this_systems_business(self) -> None:
        assert difference(Money.of("100.00"), Money.of("152.00")) == Money.of("0.00")


class TestTheRateCard:
    """Card P2-03: a bad card fails at startup, naming the key."""

    def test_a_missing_zone_is_refused_when_used(self) -> None:
        with pytest.raises(ConfigError, match="no zone 'Z'"):
            CARD.rate_for("Z")

    def test_an_empty_zone_list_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="zones is empty"):
            RateCard(version="v", volumetric_divisor=5000, slab_grams=500, zones=())

    def test_a_negative_rate_is_refused(self) -> None:
        with pytest.raises(Exception, match="greater than 0"):
            ZoneRate(zone="A", rate_paise_per_slab=-1)

    def test_a_zero_divisor_is_refused(self) -> None:
        with pytest.raises(Exception, match="greater than 0"):
            RateCard(
                version="v",
                volumetric_divisor=0,
                slab_grams=500,
                zones=(ZoneRate(zone="A", rate_paise_per_slab=3000),),
            )

    def test_a_zero_slab_is_refused(self) -> None:
        with pytest.raises(Exception, match="greater than 0"):
            RateCard(
                version="v",
                volumetric_divisor=5000,
                slab_grams=0,
                zones=(ZoneRate(zone="A", rate_paise_per_slab=3000),),
            )

    def test_a_repeated_zone_is_refused(self) -> None:
        """Two rows for one zone means the rate depends on which was read first."""
        with pytest.raises(ConfigError, match="repeats"):
            RateCard(
                version="v",
                volumetric_divisor=5000,
                slab_grams=500,
                zones=(
                    ZoneRate(zone="A", rate_paise_per_slab=3000),
                    ZoneRate(zone="A", rate_paise_per_slab=4000),
                ),
            )

    def test_rates_are_whole_paise(self) -> None:
        """So rate x slabs is an integer and no rounding exists (money-rules section 7)."""
        for zone in CARD.zones:
            assert isinstance(zone.rate_paise_per_slab, int)

    def test_the_shipped_card_is_loadable(self) -> None:
        assert load_rate_card(None) is DEFAULT_RATE_CARD
        assert load_rate_card(CARD.model_dump()) == CARD

    def test_a_nonsense_card_names_the_problem(self) -> None:
        with pytest.raises(ConfigError, match="rate_card"):
            load_rate_card("not a mapping")

    def test_no_tariff_figure_is_hard_coded_in_the_calculator(self) -> None:
        """D2: this repository is independent and synthetic. A rate or divisor baked into
        arithmetic reads as a claim about somebody's real tariff.

        Allowed: 0 and 1, which are arithmetic rather than figures, and the single named
        constant for the cm3-to-litre conversion, which is a unit and not a price.
        """
        allowed = {0, 1, GRAMS_PER_CM3_NUMERATOR}
        found = {
            n.value
            for n in ast.walk(_calculator_ast())
            if isinstance(n, ast.Constant)
            and isinstance(n.value, int)
            and not isinstance(n.value, bool)
            and n.value not in allowed
        }
        assert not found, f"tariff-shaped literals in the calculator: {sorted(found)}"


class TestDecimalIsUsedThroughout:
    def test_dimensions_are_decimals(self) -> None:
        dimensions = Dimensions.of("30", "22", "15")
        assert isinstance(dimensions.length_cm, Decimal)
        assert dimensions.volume_cm3 == Decimal("9900")


def _calculator_ast() -> ast.Module:
    source = (
        Path(__file__).resolve().parents[3] / "src/dispute_agent/packs/weight_dispute/calculator.py"
    ).read_text(encoding="utf-8")
    return ast.parse(source)


def _inside_isinstance(tree: ast.Module, target: ast.Name) -> bool:
    """True when this `float` is an argument to isinstance - the guard that rejects floats."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "isinstance"):
            continue
        if any(target is inner for arg in node.args for inner in ast.walk(arg)):
            return True
    return False
