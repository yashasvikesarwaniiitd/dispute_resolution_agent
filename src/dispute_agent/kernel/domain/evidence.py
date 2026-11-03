"""Evidence and assessment (kernel-interfaces.md sections 4.5 and 4.6).

The rule that shapes this file: evidence is never invented. `EvidenceResult` carries a value
only when its status is FOUND, so "we did not find out" and "we found nothing" stay different
answers all the way to the calculator. Guessing one for the other is how a dispute gets
decided on a fact nobody actually has.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.enums import EvidenceStatus, Provenance
from dispute_agent.kernel.domain.errors import DomainValidationError, NotFound
from dispute_agent.kernel.domain.ids import AssessmentId, CaseId, EvidenceItemId, PayloadHash
from dispute_agent.kernel.domain.json_values import JsonValue
from dispute_agent.kernel.domain.values import Money, UntrustedText, Weight, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


class EvidenceRequirement(BaseModel):
    """One thing the pack wants to know, and which source serves it."""

    model_config = _FROZEN

    key: str
    source: str
    required: bool


class EvidenceResult(BaseModel):
    """What one source returned for one requirement.

    `untrusted` holds any free text that was inside the value, kept apart rather than inlined
    (I5). A shipment note written by a human at a hub is still outside text.
    """

    model_config = _FROZEN

    requirement_key: str
    source: str
    status: EvidenceStatus
    value: Mapping[str, JsonValue] | None = None
    untrusted: tuple[UntrustedText, ...] = ()
    error_code: str | None = None
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "retrieved_at")

    @model_validator(mode="after")
    def _value_iff_found(self) -> Self:
        if (self.value is None) is (self.status is EvidenceStatus.FOUND):
            raise DomainValidationError(
                f"a value is set exactly when status is FOUND; status={self.status.value}"
            )
        if (self.error_code is None) is (self.status is EvidenceStatus.FAILED):
            raise DomainValidationError(
                f"error_code is set exactly when status is FAILED; status={self.status.value}"
            )
        return self


class EvidenceItem(BaseModel):
    """An EvidenceResult once persisted."""

    model_config = _FROZEN

    id: EvidenceItemId
    case_id: CaseId
    gather_round: Annotated[int, Field(ge=0)]
    provenance: Provenance
    result: EvidenceResult
    content_hash: PayloadHash


class EvidenceBundle(BaseModel):
    """Everything gathered in one round."""

    model_config = _FROZEN

    case_id: CaseId
    gather_round: Annotated[int, Field(ge=0)]
    items: tuple[EvidenceItem, ...] = ()

    def by_key(self, key: str) -> EvidenceItem:
        """Raises NotFound, rather than returning None, because every caller needs the item."""
        for item in self.items:
            if item.result.requirement_key == key:
                return item
        raise NotFound(f"no evidence item for requirement {key!r} in round {self.gather_round}")


class Fact(BaseModel):
    """A number a message may quote (I9). Messages cite facts by fact_id, never by value.

    `source_items` may be empty for a value that came from configuration, such as a rate-card
    constant. It is never empty for something measured.
    """

    model_config = _FROZEN

    fact_id: str
    label: str
    value: Money | Weight | Decimal | str | int
    source_items: tuple[EvidenceItemId, ...] = ()


class EligibilityResult(BaseModel):
    model_config = _FROZEN

    outcome: Literal["eligible", "ineligible", "unknown"]
    reason_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _reasons_unless_eligible(self) -> Self:
        if self.outcome != "eligible" and not self.reason_codes:
            raise DomainValidationError(f"{self.outcome} needs at least one reason code")
        return self


class ChargeAssessment(BaseModel):
    """Deterministic output of DomainPack.assess. Same bundle in, same assessment out.

    `input_hash` is what makes that checkable: it is the hash of the bundle the assessment was
    computed from, so a stored assessment can be shown to belong to the evidence it claims.
    """

    model_config = _FROZEN

    id: AssessmentId
    case_id: CaseId
    gather_round: Annotated[int, Field(ge=0)]
    pack_version: str
    eligible: Literal["eligible", "ineligible", "unknown"]
    reason_codes: tuple[str, ...] = ()
    charged: Money | None = None
    recalculated: Money | None = None
    discrepancy: Money | None = None
    #: The corrected charges behind the other two provable reasons (money-rules.md section
    #: 3, applied to kernel-interfaces.md section 13). Without them a `remeasurement_lower`
    #: or `duplicate_charge` credit has no amount: `discrepancy` is charged minus
    #: recalculated, which is zero by definition when the error is not a calculation error.
    recalculated_remeasured: Money | None = None
    duplicate_debit_total: Money | None = None
    definite_errors: tuple[str, ...] = ()
    facts: tuple[Fact, ...] = ()
    input_hash: PayloadHash

    @model_validator(mode="after")
    def _check_facts_and_discrepancy(self) -> Self:
        ids = [fact.fact_id for fact in self.facts]
        if len(set(ids)) != len(ids):
            raise DomainValidationError("fact_id must be unique within an assessment")
        if self.charged is None or self.recalculated is None:
            if self.discrepancy is not None:
                raise DomainValidationError(
                    "discrepancy needs both charged and recalculated to be known"
                )
            return self
        expected = (
            self.charged - self.recalculated
            if self.charged >= self.recalculated
            else self.recalculated - self.charged
        )
        if self.discrepancy != expected:
            raise DomainValidationError(
                f"discrepancy {self.discrepancy} is not |charged - recalculated| ({expected})"
            )
        return self
