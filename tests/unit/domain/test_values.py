"""Card P0-03: Money, Weight and UntrustedText.

The float tests are the point of this file. I9 says money and weight calculations are
deterministic; a float silently makes them not, and the failure shows up as a rupee
discrepancy in a seller's email months later.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money, UntrustedText, Weight


class TestMoney:
    def test_paise_round_trip(self) -> None:
        assert Money.from_paise(15200).to_paise() == 15200
        assert Money.from_paise(15200).amount == Decimal("152.00")

    @pytest.mark.parametrize("value", ["152.00", "152.0", "152", 152, Decimal("152.00")])
    def test_equal_forms_compare_equal(self, value: str | int | Decimal) -> None:
        """ "152.5" and "152.50" are the same amount; quantizing makes == mean that."""
        assert Money.of(value) == Money.of("152.00")

    def test_float_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="float"):
            Money.of(152.00)  # type: ignore[arg-type]

    def test_too_many_places_is_refused(self) -> None:
        """No rounding inside the kernel, ever: the pack decides, explicitly."""
        with pytest.raises(DomainValidationError, match="decimal places"):
            Money.of("152.005")

    def test_negative_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="negative"):
            Money.of("-1.00")

    def test_subtraction_cannot_go_negative(self) -> None:
        with pytest.raises(DomainValidationError, match="negative"):
            Money.of("10.00") - Money.of("10.01")

    def test_arithmetic(self) -> None:
        assert Money.of("190.00") - Money.of("152.00") == Money.of("38.00")
        assert Money.of("1.50") + Money.of("2.25") == Money.of("3.75")

    def test_ordering(self) -> None:
        assert Money.of("1.00") < Money.of("2.00")
        assert Money.of("2.00") >= Money.of("2.00")

    def test_frozen(self) -> None:
        money = Money.of("1.00")
        with pytest.raises(ValidationError):
            money.amount = Decimal("2.00")

    def test_high_value_fixture_arithmetic(self) -> None:
        """synthetic-data.md calc_error_high_value: 26 slabs x INR 70.00 = INR 1,820.00.

        Card P2-02 warns this is where per-slab rounding would show up as a rupee error.
        """
        credit = Money.of("2800.00") - Money.of("980.00")
        assert credit == Money.of("1820.00")
        assert credit.to_paise() == 182000


class TestWeight:
    def test_gram_round_trip(self) -> None:
        assert Weight.from_grams(1980).to_grams() == 1980
        assert Weight.from_grams(1980).kg == Decimal("1.980")

    def test_float_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="float"):
            Weight.of(1.98)  # type: ignore[arg-type]

    def test_four_places_is_refused(self) -> None:
        with pytest.raises(DomainValidationError, match="decimal places"):
            Weight.of("1.9805")

    def test_kurta_fixture(self) -> None:
        """declared 480 g, measured volumetric 1,980 g (synthetic-data.md)."""
        assert Weight.from_grams(480) < Weight.from_grams(1980)


class TestUntrustedText:
    def test_truncation_is_recorded(self) -> None:
        """The interpreter must know when it did not see the whole reply."""
        long = UntrustedText.create("x" * 25_000, "seller_email", "msg_1")
        assert long.truncated is True
        assert len(long.text) == 20_000

    def test_short_text_is_not_marked_truncated(self) -> None:
        short = UntrustedText.create("hello", "seller_email", "msg_1")
        assert short.truncated is False
        assert short.content_hash.startswith("sha256:")

    def test_hash_is_over_the_stored_text(self) -> None:
        """Two texts differing only past the cap hash the same, because that is what was kept."""
        a = UntrustedText.create("x" * 20_000 + "A", "seller_email", "msg_1")
        b = UntrustedText.create("x" * 20_000 + "B", "seller_email", "msg_2")
        assert a.content_hash == b.content_hash

    def test_frozen(self) -> None:
        text = UntrustedText.create("hello", "seller_email", "msg_1")
        with pytest.raises(ValidationError):
            text.text = "goodbye"
