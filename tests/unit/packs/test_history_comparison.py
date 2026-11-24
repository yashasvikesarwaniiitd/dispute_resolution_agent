"""The historical comparison (ADR 0060): who counts as a reference, and what it concludes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from dispute_agent.packs.weight_dispute.history import Reading, compare

NOW = datetime(2026, 11, 20, 10, tzinfo=UTC)
CASE = (Decimal("19"), Decimal("11"), Decimal("2"))


def reading(
    ref: str,
    sides: tuple[str, str, str] | None = ("19", "11", "2"),
    grams: int | None = 72,
    *,
    days_ago: int = 10,
    seller: str = "S1",
    sku: str = "CASE",
    pkg: str = "MAILER",
    qty: int = 1,
    status: str = "trusted",
) -> Reading:
    return Reading(
        ref=ref,
        seller_id=seller,
        sku_id=sku,
        packaging_id=pkg,
        quantity=qty,
        measured_at=NOW - timedelta(days=days_ago),
        sides=tuple(Decimal(s) for s in sides) if sides else None,  # type: ignore[arg-type]
        grams=grams,
        measurement_status=status,
    )


def history(n: int = 6, **kw: object) -> list[Reading]:
    return [reading(f"H{i}", days_ago=10 + i, **kw) for i in range(n)]  # type: ignore[arg-type]


def current(
    sides: tuple[str, str, str] | None = ("34", "22", "15"), grams: int | None = 72
) -> Reading:
    return reading("NOW", sides, grams, days_ago=0)


class TestWhoCountsAsAReference:
    def test_only_the_same_seller(self) -> None:
        others = history(6, seller="S2")
        assert compare(current(), others)["verdict"] == "insufficient_history"

    def test_only_the_same_product_packaging_and_quantity(self) -> None:
        assert compare(current(), history(6, sku="OTHER"))["verdict"] == "insufficient_history"
        result = compare(current(), history(6, pkg="BOX", qty=3))
        assert result["verdict"] == "not_comparable"
        assert result["differs_by"] == ["packaging", "quantity"]

    def test_never_the_parcel_itself(self) -> None:
        result = compare(current(), [*history(5), current()])
        assert "NOW" not in result["reference_refs"] and result["references"] == 5

    def test_never_a_reading_taken_later(self) -> None:
        later = [reading(f"L{i}", days_ago=-1 - i) for i in range(6)]
        result = compare(current(), later)
        assert result["verdict"] == "insufficient_history"
        assert result["excluded"] == {"measured_later": 6}

    def test_disputed_and_corrected_readings_are_left_out_and_counted(self) -> None:
        others = [
            *history(5),
            reading("D", ("34", "22", "15"), status="disputed"),
            reading("C", ("34", "22", "15"), status="corrected"),
        ]
        result = compare(current(), others)
        assert set(result["reference_refs"]).isdisjoint({"D", "C"})
        assert result["excluded"] == {"disputed": 1, "corrected": 1}

    def test_a_box_scanned_on_its_side_still_matches(self) -> None:
        rotated = [reading(f"R{i}", ("2", "19", "11"), days_ago=5 + i) for i in range(6)]
        assert (
            compare(reading("NOW", ("11", "2", "19"), days_ago=0), rotated)["verdict"] == "normal"
        )

    def test_an_outlier_is_listed_and_kept_out_of_the_range(self) -> None:
        others = [*history(6), reading("BIG", ("40", "30", "20"), days_ago=30)]
        result = compare(reading("NOW", days_ago=0), others)
        assert result["outlier_refs"] == ["BIG"] and "BIG" not in result["reference_refs"]


class TestTheVerdict:
    def test_a_much_larger_reading_is_anomalous(self) -> None:
        result = compare(current(), history(6))
        assert result["verdict"] == "anomalous" and result["references"] == 6

    def test_a_reading_inside_the_usual_range_is_normal(self) -> None:
        assert (
            compare(reading("NOW", ("19.2", "11", "2"), days_ago=0), history(6))["verdict"]
            == "normal"
        )

    def test_fewer_than_five_references_is_insufficient(self) -> None:
        result = compare(current(), history(4))
        assert result["verdict"] == "insufficient_history" and result["references"] == 4

    def test_missing_measurements_say_so(self) -> None:
        assert compare(current(sides=None), history(6))["verdict"] == "missing_evidence"
        assert compare(current(grams=None), history(6))["verdict"] == "missing_evidence"

    def test_bigger_but_inside_the_range_is_not_an_anomaly(self) -> None:
        # Above 1.25x the median (418 cm3), but a past reading (581, inside the 1.5x outlier
        # band) was as large: not beyond what history shows.
        varied = [*history(5), reading("WIDE", ("22", "12", "2.2"), days_ago=40)]
        result = compare(reading("NOW", ("21.5", "12", "2.2"), days_ago=0), varied)
        assert result["verdict"] == "normal"


class TestTheFlags:
    def test_a_declaration_well_below_history(self) -> None:
        lamps = history(6, sides=("40", "15", "15"), grams=1420)
        result = compare(
            reading("NOW", ("40.2", "15.1", "15"), 1418, days_ago=0),
            lamps,
            declared_sides=(Decimal("30"), Decimal("12"), Decimal("12")),
        )
        assert result["verdict"] == "normal" and result["flags"]["declared_below_history"] is True

    def test_no_under_declaration_claim_without_a_full_pool(self) -> None:
        result = compare(
            current(), history(2), declared_sides=(Decimal("1"), Decimal("1"), Decimal("1"))
        )
        assert result["flags"]["declared_below_history"] is False

    def test_the_packaging_recorded_at_booking_must_fit_what_was_measured(self) -> None:
        carton = (Decimal("35"), Decimal("30"), Decimal("12"))
        fits = compare(
            reading("NOW", ("35.1", "30", "12"), 932, days_ago=0), [], packaging_sides=carton
        )
        assert fits["flags"]["packaging_consistent"] is True
        mailer_size = compare(
            reading("NOW", ("19", "11", "2"), 72, days_ago=0), [], packaging_sides=carton
        )
        assert mailer_size["flags"]["packaging_consistent"] is False

    def test_numbers_are_text_so_nothing_is_a_float(self) -> None:
        result = compare(current(), history(6))
        assert result["typical"]["sides_cm"] == ["19", "11", "2"]
        assert all(isinstance(v, str) for v in result["ratio_to_typical"].values())
