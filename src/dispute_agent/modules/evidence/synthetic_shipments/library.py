"""The synthetic shipment library: every fixture, served by shipment reference (runtime wiring).

The scenario suite gives each run one fixture. The long-running app takes disputes for any
shipment, so this source finds the fixture whose shipment the dispute names and answers from it
exactly as `SyntheticShipmentSource` does. A shipment that no fixture describes is MISSING for
every requirement - never invented (I11, the source contract).

Two fixtures share a shipment and claim (`kurta_box_larger` and `calc_error_wrong_slab`, both
SHP-000001 / CLM-000001): the first in `FIXTURES` order serves it. The scenario suite is not
affected - it picks its fixture by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

from dispute_agent.modules.evidence.synthetic_shipments.fixtures import (
    FIXTURES,
    cluster_members,
    fixture,
)
from dispute_agent.modules.evidence.synthetic_shipments.source import (
    MODULE,
    SyntheticShipmentSource,
)

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import EvidenceRequirement, EvidenceResult
    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.ports import Clock


@dataclass
class SyntheticLibrarySource:
    """Implements EvidenceSource over every fixture."""

    clock: Clock | None = None
    name: str = MODULE
    _by_shipment: dict[str, SyntheticShipmentSource] = field(default_factory=dict)
    _unknown: SyntheticShipmentSource | None = None

    def __post_init__(self) -> None:
        for fixture_name in FIXTURES:
            built = fixture(fixture_name)
            self._by_shipment.setdefault(
                built.shipment.ref, SyntheticShipmentSource(fixture=built, clock=self.clock)
            )
            # Phase 7 cluster fixtures: each member parcel is its own dispute (P7-03).
            for member in cluster_members(built):
                self._by_shipment.setdefault(
                    member.ref,
                    SyntheticShipmentSource(
                        fixture=replace(built, shipment=member), clock=self.clock
                    ),
                )
        # No fixture: every answer is MISSING, which is the honest answer.
        self._unknown = SyntheticShipmentSource(fixture=None, clock=self.clock)

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        source = self._by_shipment.get(facts.shipment_ref, self._unknown)
        assert source is not None
        return source.fetch(case_id, facts, requirement)

    def shipments(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_shipment))

    def keys_for(self, shipment_ref: str) -> dict[str, str]:
        """A shipment's cluster keys for cross-case matching (P7-02, wired in app.py).

        Raises LookupError for a shipment no fixture describes: unknown is not "no keys", and
        the pattern provider turns it into caution rather than a silent miss.
        """
        source = self._by_shipment.get(shipment_ref)
        if source is None or source.fixture is None:
            raise LookupError(f"no synthetic shipment {shipment_ref!r}")
        shipment = source.fixture.shipment
        return {"scanner": shipment.scanner_id, "hub": shipment.hub_id, "sku": shipment.sku_id}


__all__ = ["SyntheticLibrarySource"]
