"""Card P2-04: the generated world is the same every time, and every fixture exists.

The determinism test is the one that matters. A scenario that fails on Tuesday and passes on
Wednesday is worse than no scenario, and the whole eval suite rests on "the same seed gives
the same world". It is checked byte for byte rather than by sampling a few fields.

`test_fixtures_exist` reads `synthetic-data.md` and `eval-scenarios.yaml` **at test time**, so
a scenario cannot name a fixture nobody built. That is the failure this guards: the scenario
catalogue and the generator drifting apart until a Phase 6 eval run dies on a KeyError.
"""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import (
    FINDINGS,
    FIXTURES,
    REPLIES,
    fixture,
)
from dispute_agent.modules.evidence.synthetic_shipments.generator import (
    DEFAULT_SEED,
    SEED_OFFSETS,
    build_world,
)

ROOT = Path(__file__).resolve().parents[4]
SYNTHETIC = (ROOT / "docs" / "spec" / "synthetic-data.md").read_text(encoding="utf-8")
SCENARIOS = yaml.safe_load(
    (ROOT / "docs" / "spec" / "eval-scenarios.yaml").read_text(encoding="utf-8")
)


def _fingerprint(world: object) -> str:
    """Everything about a world, as one comparable string."""
    import json

    return json.dumps(world, default=str, sort_keys=True)


class TestDeterminism:
    def test_the_same_seed_gives_the_same_world(self) -> None:
        """A scenario that fails on Tuesday and passes on Wednesday is worse than none."""
        first = build_world(seed=DEFAULT_SEED, shipments=50)
        second = build_world(seed=DEFAULT_SEED, shipments=50)
        assert _fingerprint(first) == _fingerprint(second)

    def test_a_different_seed_gives_a_different_world(self) -> None:
        """The positive control: if seeding did nothing, the test above would pass anyway."""
        assert _fingerprint(build_world(seed=1, shipments=50)) != _fingerprint(
            build_world(seed=2, shipments=50)
        )

    def test_each_family_has_its_own_stream(self) -> None:
        """So adding a knob to one family cannot shift every existing fixture.

        A single shared stream would make every parcel change the day somebody adds a seller
        field, and every reviewed fixture would silently become a different case.
        """
        assert len(set(SEED_OFFSETS.values())) == len(SEED_OFFSETS)
        more_sellers = build_world(seed=DEFAULT_SEED, sellers=80, shipments=50)
        base = build_world(seed=DEFAULT_SEED, sellers=40, shipments=50)
        assert more_sellers.hubs == base.hubs

    def test_ids_are_the_documented_shapes(self) -> None:
        world = build_world(shipments=5)
        assert world.sellers[0].id == "SELL-0001"
        assert world.skus[0].id == "SKU-0001"
        assert world.hubs[0].id == "HUB-01"
        assert world.hubs[0].scanners[0].id == "SCN-01-1"
        assert world.shipments[0].ref == "SHP-000001"
        assert world.shipments[0].claim_ref == "CLM-000001"


class TestTheWorldIsSynthetic:
    def test_every_seller_is_on_a_test_domain(self) -> None:
        """I11: a generated contact must never be something that could reach a real person."""
        world = build_world(shipments=5)
        assert all(s.contact.endswith("@example.invalid") for s in world.sellers)

    def test_no_float_is_generated(self) -> None:
        """Weight in grams, money in paise, dimensions as Decimal (ADR 0011)."""
        world = build_world(shipments=20)
        for shipment in world.shipments:
            assert isinstance(shipment.billed_paise, int)
            assert isinstance(shipment.measured_dead_grams, int)
            # Asserting `not isinstance(..., float)` would be vacuous - mypy knows a
            # Decimal can never be a float. Assert what is actually wanted instead.
            assert isinstance(shipment.measured.length_cm, Decimal)

    def test_a_faulty_scanner_is_never_random(self) -> None:
        """synthetic-data.md: a bias is a fixture knob, so a world is never accidentally
        full of mismeasured parcels."""
        world = build_world(shipments=20)
        assert all(s.bias_mm == 0 for hub in world.hubs for s in hub.scanners)

    def test_most_shipments_are_delivered(self) -> None:
        world = build_world(shipments=200)
        delivered = [s for s in world.shipments if s.delivered]
        assert len(delivered) > len(world.shipments) * 0.9

    def test_every_shipment_has_a_debit(self) -> None:
        world = build_world(shipments=20)
        for shipment in world.shipments:
            assert any(entry.kind == "DEBIT" for entry in shipment.ledger)


class TestFixturesExist:
    def test_every_fixture_named_in_the_spec_is_constructible(self) -> None:
        """Read from synthetic-data.md at test time, so the two cannot drift."""
        section = SYNTHETIC.split("| Fixture | Setup |")[1].split("\n\n")[0]
        named = set(re.findall(r"^\| `([a-z_0-9]+)`", section, flags=re.MULTILINE))
        assert named, "parsed no fixture names out of synthetic-data.md section 4"
        missing = named - set(FIXTURES)
        assert not missing, f"named in the spec but not built: {sorted(missing)}"

    @pytest.mark.parametrize("name", sorted(FIXTURES))
    def test_each_one_builds(self, name: str) -> None:
        built = fixture(name)
        assert built.name == name
        assert built.shipment.ref.startswith("SHP-")

    def test_every_fixture_a_scenario_references_exists(self) -> None:
        """A scenario naming a fixture nobody built would die in a Phase 6 eval run."""
        referenced = {
            scenario["fixture"]
            for scenario in SCENARIOS["scenarios"]
            if isinstance(scenario.get("fixture"), str)
        }
        missing = referenced - set(FIXTURES)
        assert not missing, f"scenarios reference unbuilt fixtures: {sorted(missing)}"

    def test_an_unknown_fixture_raises_rather_than_inventing_one(self) -> None:
        with pytest.raises(DomainValidationError, match="no fixture"):
            fixture("not_a_fixture")

    def test_the_reply_and_finding_banks_cover_what_the_spec_names(self) -> None:
        for name in ("photo_shows_large_box", "withdrawal_text", "jailbreak", "any"):
            assert name in REPLIES
        for name in ("remeasured_lower", "no_fault_found", "inconclusive_still_checking"):
            assert name in FINDINGS

    def test_an_inconclusive_finding_carries_no_measurements(self) -> None:
        """It is what leaves the remeasurement expectation open, which makes `wait`
        reachable at all (S31)."""
        assert FINDINGS["inconclusive_still_checking"].measured is None


class TestTheNumbersInTheNamedFixtures:
    """The arithmetic a reader should be able to check by hand."""

    def test_kurta_is_billed_152_rupees(self) -> None:
        built = fixture("kurta_box_larger")
        assert Money.from_paise(built.shipment.billed_paise) == Money.of("152.00")
        assert built.shipment.billed_paise == built.shipment.correct_paise

    def test_wrong_slab_is_billed_190_and_owes_38(self) -> None:
        built = fixture("calc_error_wrong_slab")
        assert Money.from_paise(built.shipment.billed_paise) == Money.of("190.00")
        overcharge = built.shipment.billed_paise - built.shipment.correct_paise
        assert Money.from_paise(overcharge) == Money.of("38.00")

    def test_high_value_owes_1820(self) -> None:
        built = fixture("calc_error_high_value")
        assert Money.from_paise(built.shipment.billed_paise) == Money.of("2800.00")
        assert Money.from_paise(built.shipment.correct_paise) == Money.of("980.00")
        overcharge = built.shipment.billed_paise - built.shipment.correct_paise
        assert Money.from_paise(overcharge) == Money.of("1820.00")

    def test_duplicate_debit_has_two_identical_debits(self) -> None:
        built = fixture("duplicate_debit")
        debits = [e for e in built.shipment.ledger if e.kind == "DEBIT"]
        assert len(debits) == 2
        assert debits[0].amount_paise == debits[1].amount_paise

    def test_multi_error_has_both_a_keyed_weight_and_a_duplicate(self) -> None:
        built = fixture("multi_error")
        assert built.shipment.billed_paise > built.shipment.correct_paise
        assert len([e for e in built.shipment.ledger if e.kind == "DEBIT"]) == 2

    def test_not_delivered_is_rto(self) -> None:
        assert fixture("not_delivered").shipment.status == "RTO"

    def test_the_non_allowlisted_seller_is_off_the_test_domain(self) -> None:
        assert not fixture("non_allowlisted_seller").seller_contact.endswith("example.invalid")

    def test_the_injected_statement_is_the_corpus_item(self) -> None:
        """Verbatim, so the screen is tested against the string the charter names."""
        built = fixture("injection_in_statement")
        assert built.statement is not None
        assert "Ignore all previous instructions" in built.statement

    def test_the_scanner_fault_finding_carries_structured_measurements(self) -> None:
        """Only structured fields can ever prove a credit (money-rules.md section 2)."""
        built = fixture("scanner_fault")
        assert built.finding is not None
        assert built.finding.kind == "remeasured"
        assert built.finding.measured is not None
