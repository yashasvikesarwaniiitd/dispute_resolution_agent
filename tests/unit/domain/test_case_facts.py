"""Card P0-04, dependency: CaseFacts (kernel-interfaces.md section 4.2).

Built in Phase 0 because CaseOutcome carries one and PatternContextProvider takes one. The
interesting tests are the ones about what a fact may *be*: no floats, because a pack fact
feeds the calculator (I9), and a seller statement that is UntrustedText rather than str,
because the whole point of section 4.2 is that the seller's own words are not facts (I5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money, UntrustedText

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


def facts(**overrides: object) -> CaseFacts:
    fields: dict[str, object] = {
        "shipment_ref": "SHP-0001",
        "claim_ref": "CHG-0001",
        "seller_ref": "SELLER-0001",
        "seller_contact": "seller@example.invalid",
        "raised_at": NOW,
    }
    fields.update(overrides)
    return CaseFacts(**fields)


class TestRefs:
    @pytest.mark.parametrize("ref", ["SHP-0001", "a", "A_b-9", "x" * 64])
    def test_accepted(self, ref: str) -> None:
        assert facts(shipment_ref=ref).shipment_ref == ref

    @pytest.mark.parametrize("ref", ["", "x" * 65, "SHP 0001", "SHP/0001", "SHP.0001", "shp\n"])
    def test_refused(self, ref: str) -> None:
        with pytest.raises(ValidationError):
            facts(shipment_ref=ref)


class TestSellerContact:
    def test_an_address_is_accepted(self) -> None:
        assert facts(seller_contact="a.b+tag@example.invalid").seller_contact

    @pytest.mark.parametrize("value", ["seller", "seller@", "@example.invalid", "a b@c.d", "a@b"])
    def test_a_non_address_is_refused(self, value: str) -> None:
        with pytest.raises(DomainValidationError):
            facts(seller_contact=value)


class TestPackFacts:
    def test_scalars_are_accepted(self) -> None:
        pack = {"hub": "HUB-07", "slabs": 26, "oversize": True, "declared_kg": Decimal("0.480")}
        assert facts(pack_facts=pack).pack_facts == pack

    def test_a_float_is_refused_by_name(self) -> None:
        """I9. The error names the key, because a pack author needs to know which one."""
        with pytest.raises(DomainValidationError, match="declared_kg"):
            facts(pack_facts={"declared_kg": 0.48})

    def test_a_nested_structure_is_refused(self) -> None:
        """Flat by design: the kernel stays domain-neutral and the pack owns the meaning."""
        with pytest.raises(ValidationError):
            facts(pack_facts={"dims": {"l": 30}})

    def test_defaults_to_empty(self) -> None:
        assert facts().pack_facts == {}


class TestTheRestOfTheFacts:
    def test_raised_at_must_be_aware(self) -> None:
        with pytest.raises(DomainValidationError):
            facts(raised_at=datetime(2026, 3, 1, 9, 30))

    def test_disputed_amount_is_money(self) -> None:
        assert facts(disputed_amount=Money.of("152.00")).disputed_amount == Money.of("152.00")

    def test_a_statement_must_be_untrusted_text(self) -> None:
        """I5: the seller's own words arrive labelled, or they do not arrive at all."""
        with pytest.raises(ValidationError):
            facts(statement="the parcel was 480 grams")

        statement = UntrustedText.create("the parcel was 480 grams", "dispute_form", "frm_1")
        assert facts(statement=statement).statement is statement

    def test_unknown_fields_are_refused(self) -> None:
        """extra='forbid': a typo in an intake adapter is a failure, not a silent drop."""
        with pytest.raises(ValidationError):
            facts(sgipment_ref="SHP-0001")

    def test_frozen(self) -> None:
        case_facts = facts()
        with pytest.raises(ValidationError):
            case_facts.seller_ref = "SELLER-0002"
