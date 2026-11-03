"""The synthetic shipment source (kernel-interfaces.md 6.4, synthetic-data.md section 5).

Three answers and no fourth: FOUND with a value, MISSING, or FAILED with a code. It **never
invents a value** — that is the whole contract of an evidence source, and the reason a
disabled one raises rather than returning MISSING (P0-06).

The fault vocabulary is here because D41 needs the distinction. A transient failure is
retried up to three times in a gather round; a permanent one is not retried at all. Getting
that the wrong way round means either hammering a dead endpoint or giving up on a blip, and
only the adapter knows which it had.

`timeout_twice` is the interesting one: it fails, fails, then works. A source that only ever
failed or only ever worked would let a broken retry loop pass its tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.evidence import EvidenceResult
from dispute_agent.kernel.domain.values import UntrustedText

if TYPE_CHECKING:
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import EvidenceRequirement
    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.ports import Clock
    from dispute_agent.modules.evidence.synthetic_shipments.fixtures import Fixture

MODULE = "evidence"
ADAPTER = "synthetic_shipments"

#: Fault tokens whose failure the gather round will wait out (D41 retries these).
TRANSIENT_FAULTS = frozenset({"timeout", "timeout_twice"})
#: Fault tokens whose failure is not worth retrying.
PERMANENT_FAULTS = frozenset({"error_permanent"})
#: The module itself is off, which is not a fault of this fetch at all (I6).
UNAVAILABLE_FAULTS = frozenset({"unavailable"})

#: What a weight dispute needs to know. Each key is served by this source.
REQUIREMENT_KEYS = (
    "shipment.status",
    "shipment.declared_dims",
    "shipment.measured_dims",
    "shipment.zone",
    "ledger.entries",
    "shipment.image_description",
    "shipment.history",
    "ops.finding",
)


@dataclass
class SyntheticShipmentSource:
    """Implements EvidenceSource. Reads a fixture, never a network.

    `faults` maps a requirement key to a fault token; anything not listed answers normally.
    `_calls` counts attempts per key, which is what `timeout_twice` needs to know.
    """

    name: str = MODULE
    fixture: Fixture | None = None
    faults: dict[str, str] = field(default_factory=dict)
    #: Whether the hub's finding has come back yet. False by default, because a finding is
    #: an answer to a remeasurement *we* asked for: it does not exist in the first gather
    #: round of a case that has not asked for one. Serving it from round 1 would quietly
    #: make `scanner_fault` prove `remeasurement_lower` before anybody remeasured anything,
    #: and S06's whole point is that round 1 has no finding, no image and no reply.
    finding_available: bool = False
    #: Injected, never read from the wall. A fixture that stamped itself with the real
    #: time would make two runs of the same scenario differ in the one field a reader
    #: would never think to look at (CLAUDE.md section 6).
    clock: Clock | None = None
    _calls: dict[str, int] = field(default_factory=dict)

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        """Raises ModuleUnavailable only; every other failure is a FAILED result.

        A flaky source should degrade the evidence, not crash the case — the gather step
        records what failed and the assessment sees it.
        """
        key = requirement.key
        attempt = self._calls.get(key, 0) + 1
        self._calls[key] = attempt
        fault = self.faults.get(key)
        now = self.clock.now() if self.clock is not None else _now()

        if fault in UNAVAILABLE_FAULTS:
            # The module is off. That is I6's business, not a FAILED item's.
            raise ModuleUnavailable(MODULE)

        if fault in PERMANENT_FAULTS:
            return self._failed(key, "error_permanent", now)

        if fault == "timeout" or (fault == "timeout_twice" and attempt <= 2):
            return self._failed(key, "timeout", now)

        if self.fixture is None:
            return self._missing(key, now)

        value = self._value_for(key, self.fixture, facts)
        if value is None:
            return self._missing(key, now)

        untrusted = self._untrusted_for(key, self.fixture)
        return EvidenceResult(
            requirement_key=key,
            source=self.name,
            status=EvidenceStatus.FOUND,
            value=value,
            untrusted=untrusted,
            retrieved_at=now,
        )

    # -- helpers ---------------------------------------------------------------------------

    def _failed(self, key: str, code: str, now: datetime) -> EvidenceResult:
        return EvidenceResult(
            requirement_key=key,
            source=self.name,
            status=EvidenceStatus.FAILED,
            error_code=code,
            retrieved_at=now,
        )

    def _missing(self, key: str, now: datetime) -> EvidenceResult:
        """ "There is nothing there" — which is not the same as "we could not find out"."""
        return EvidenceResult(
            requirement_key=key, source=self.name, status=EvidenceStatus.MISSING, retrieved_at=now
        )

    def _value_for(self, key: str, built: Fixture, facts: CaseFacts) -> dict[str, object] | None:
        shipment = built.shipment
        match key:
            case "shipment.status":
                return {"status": shipment.status}
            case "shipment.declared_dims":
                return _dims(shipment.declared) | {"dead_grams": shipment.declared_dead_grams}
            case "shipment.measured_dims":
                return _dims(shipment.measured) | {
                    "dead_grams": shipment.measured_dead_grams,
                    "scanner_id": shipment.scanner_id,
                }
            case "shipment.zone":
                return {"zone": shipment.zone, "packaging": shipment.packaging}
            case "ledger.entries":
                # This claim's entries, not the shipment's. A shipment can carry several
                # charge lines, and handing the pack all of them lets it recalculate a
                # dispute about one line against the charge for another (I9).
                return {
                    "claim_ref": facts.claim_ref,
                    "entries": [
                        {
                            "transaction_id": entry.transaction_id,
                            "kind": entry.kind,
                            "amount_paise": entry.amount_paise,
                            "claim_ref": entry.claim_ref,
                            "reason_code": entry.reason_code,
                        }
                        for entry in shipment.ledger
                        if entry.claim_ref == facts.claim_ref
                    ],
                }
            case "shipment.image_description":
                if built.image_description is None:
                    return None
                # The sentence itself is deliberately NOT in here. It is written by a
                # person, and agent-design.md section 1 renders a SYSTEM item's `value` in
                # the model's *trusted* block: a description left in the value would arrive
                # as fact. It travels as UntrustedText instead (`_untrusted_for`), which is
                # the whole of I5 for this key.
                return {"present": True}
            case "shipment.history":
                return {"entries": list(built.history)} if built.history else None
            case "ops.finding":
                finding = built.finding
                if finding is None or not self.finding_available:
                    return None
                value: dict[str, object] = {"kind": finding.kind}
                if finding.measured is not None:
                    value |= _dims(finding.measured)
                if finding.measured_dead_grams is not None:
                    value["dead_grams"] = finding.measured_dead_grams
                return value
            case _:
                return None

    def _untrusted_for(self, key: str, built: Fixture) -> tuple[UntrustedText, ...]:
        """Free text inside a value, kept apart rather than inlined (I5).

        An ops note and an image description are written by people. They travel labelled from
        the moment they enter, so nothing downstream can mistake one for a measurement.
        """
        if key == "ops.finding" and built.finding is not None and built.finding.note:
            return (
                UntrustedText.create(built.finding.note, "jira_comment", f"finding:{built.name}"),
            )
        if key == "shipment.image_description" and built.image_description is not None:
            return (
                UntrustedText.create(
                    built.image_description, "shipment_note", f"image:{built.shipment.ref}"
                ),
            )
        return ()


def _dims(dimensions: object) -> dict[str, object]:
    """Dimensions as strings, so a Decimal survives JSON without becoming a float (I9)."""
    return {
        "length_cm": str(dimensions.length_cm),  # type: ignore[attr-defined]
        "width_cm": str(dimensions.width_cm),  # type: ignore[attr-defined]
        "height_cm": str(dimensions.height_cm),  # type: ignore[attr-defined]
    }


def _now() -> datetime:
    from datetime import UTC
    from datetime import datetime as _datetime

    return _datetime.now(UTC)


#: What each fault token comes out as. The tokens are *inputs* a test injects; the codes are
#: what a FAILED result carries, and they are not the same vocabulary. `timeout_twice` emits
#: the code `timeout`, so a function answering "is this retryable?" from the token set was
#: answering about something no result ever contains. The retry decision has one home:
#: `kernel.orchestrator.evidence.is_retryable`, which reads the code.
FAULT_CODES: dict[str, str] = {
    "timeout": "timeout",
    "timeout_twice": "timeout",
    "error_permanent": "error_permanent",
}


__all__ = [
    "ADAPTER",
    "FAULT_CODES",
    "MODULE",
    "PERMANENT_FAULTS",
    "REQUIREMENT_KEYS",
    "TRANSIENT_FAULTS",
    "SyntheticShipmentSource",
]
