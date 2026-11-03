"""Core domain models (kernel-interfaces.md sections 4.1 and 4.3).

The spec heads section 4 "domain/models.py, domain/events.py". Everything in section 4 does
not fit one readable file, so it is split by topic — this module, `evidence.py`,
`reasoning.py`, `actions.py`, `external.py`, `events.py` — keeping the spec's class names
exactly. CLAUDE.md section 7: files rarely exceed 300 lines.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    STATE_CATEGORY,
    ActorKind,
    CaseState,
    CauseKind,
    ReviewCode,
    StateCategory,
)
from dispute_agent.kernel.domain.enums import TERMINAL_STATES as _TERMINAL
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, IntentId
from dispute_agent.kernel.domain.values import Money, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


class Actor(BaseModel):
    """Who did it. `id` is "system", "human:<user id>", or a source name for EXTERNAL."""

    model_config = _FROZEN

    kind: ActorKind
    id: Annotated[str, Field(min_length=1, max_length=120)]


class Cause(BaseModel):
    """Why something happened. Every Event and every state change has one (I8)."""

    model_config = _FROZEN

    kind: CauseKind
    ref: str


class ReviewReason(BaseModel):
    """Why a case is with a person.

    `detail` is staff-facing and deliberately narrow: no untrusted text and no secrets (I5,
    I12). A seller's own words never reach this field, because it is rendered in the console
    and in logs without a trust label.
    """

    model_config = _FROZEN

    code: ReviewCode
    module: str | None = None
    detail: Annotated[str, Field(max_length=300)] = ""

    @model_validator(mode="after")
    def _module_iff_module_unavailable(self) -> Self:
        needed = self.code is ReviewCode.MODULE_UNAVAILABLE
        if needed and not self.module:
            raise DomainValidationError("module_unavailable needs the module that was unavailable")
        if not needed and self.module is not None:
            raise DomainValidationError(f"{self.code.value} does not take a module name")
        return self

    def render(self) -> str:
        """ "module_unavailable:email", or the bare code (I6)."""
        return f"{self.code.value}:{self.module}" if self.module else self.code.value


class RelatedCase(BaseModel):
    """Another case for the same seller or shipment, as the briefing shows it.

    Outcome only, deliberately. A reasoner that could read a related case's evidence would
    be reasoning about a dispute nobody asked it to decide.
    """

    model_config = _FROZEN

    case_id: CaseId
    state: CaseState
    closed_at: datetime | None = None
    discrepancy: Money | None = None


class Case(BaseModel):
    """One dispute, as the kernel holds it.

    `version` is the optimistic-concurrency token and only `CaseRepository.save` bumps it.
    Everything else here is set by the state machine, which is the only thing allowed to move
    a case (I8).
    """

    model_config = _FROZEN

    id: CaseId
    pack: str
    facts: CaseFacts
    state: CaseState
    version: Annotated[int, Field(ge=1)]
    correlation_id: CorrelationId
    opened_at: datetime
    updated_at: datetime
    state_entered_at: datetime
    closed_at: datetime | None = None
    review_reason: ReviewReason | None = None
    pending_intent_id: IntentId | None = None
    action_seq: Annotated[int, Field(ge=0)] = 0
    reopen_count: Annotated[int, Field(ge=0)] = 0
    rejection_count: Annotated[int, Field(ge=0)] = 0
    gather_round: Annotated[int, Field(ge=0)] = 0
    prior_case_id: CaseId | None = None
    superseded_by_case_id: CaseId | None = None

    @field_validator("opened_at", "updated_at", "state_entered_at", "closed_at")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else require_aware(value, "case timestamp")

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        if (self.closed_at is None) is (self.state in _TERMINAL):
            raise DomainValidationError(
                f"closed_at must be set exactly for a terminal state; "
                f"state={self.state.value} closed_at={self.closed_at}"
            )
        if (self.review_reason is None) is (self.state is CaseState.NEEDS_HUMAN_REVIEW):
            raise DomainValidationError(
                "review_reason must be set exactly when the state is NEEDS_HUMAN_REVIEW; "
                f"state={self.state.value}"
            )
        if self.updated_at < self.opened_at:
            raise DomainValidationError(
                f"updated_at {self.updated_at.isoformat()} precedes "
                f"opened_at {self.opened_at.isoformat()}"
            )
        return self

    @property
    def is_terminal(self) -> bool:
        return self.state in _TERMINAL

    @property
    def category(self) -> StateCategory:
        return STATE_CATEGORY[self.state]
