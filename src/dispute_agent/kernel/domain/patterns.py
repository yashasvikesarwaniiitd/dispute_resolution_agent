"""The cross-case socket (kernel-interfaces.md 4.7 and 4.9, FLOW.md section 6, D3, I7).

Nothing populates these models until Phase 7. They exist now because of D3: the orchestrator
already calls `patterns_for`, the briefing already has a `pattern_context` field, the policy
already accepts `CautionSignal`s, and closing cases already notify `on_outcome` — all wired
to Null adapters. Connecting the real module later must change zero lines in `kernel/`, and
that is only true if the shapes it will speak through are settled first.

The invariant this file carries is I7: a pattern may only ever make the system more careful.
That is enforced by what `CautionSignal` can say, not by a rule applied to it. `min_verdict`
cannot be `ALLOW`, and there is deliberately no field for a maximum — so "this pattern says
it is fine" is not a sentence the type can form. A Phase 7 adapter cannot get it wrong,
because it cannot express it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import RESOLVED_STATES, TERMINAL_STATES, CaseState
from dispute_agent.kernel.domain.enums import VerdictKind as V
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import AssessmentId, CaseId, OutcomeId, PatternId
from dispute_agent.kernel.domain.values import Money, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)

MAX_PATTERN_SUMMARY_CHARS = 400
MAX_SIGNAL_REASON_CHARS = 200

PatternStatus = Literal["UNCONFIRMED", "CONFIRMED", "DISMISSED"]

SignalKind = Literal[
    "pattern_match",
    "pattern_context_unavailable",
    "screen_flag",
    "repeat_dispute",
    "pattern_changed_recommendation",
]


class PatternRecord(BaseModel):
    """A cross-case hypothesis (Phase 7 populates; the model exists from Phase 0).

    `summary` may be written by code or by the `pattern_analyst` prompt. When it came from a
    model it is untrusted (agent-design.md section 1): this model carries the text, and the
    briefing builder decides the trust label it is rendered under. Nothing downstream may
    treat a summary as a verified fact — a pattern is a hypothesis about other cases, and I7
    is what keeps it from becoming a reason to act.
    """

    model_config = _FROZEN

    id: PatternId
    status: PatternStatus
    summary: Annotated[str, Field(min_length=1, max_length=MAX_PATTERN_SUMMARY_CHARS)]
    matched_on: dict[str, str]
    related_case_ids: tuple[CaseId, ...]
    first_seen_at: datetime
    last_seen_at: datetime

    @field_validator("first_seen_at", "last_seen_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "pattern timestamp")

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.last_seen_at < self.first_seen_at:
            raise DomainValidationError(
                f"last_seen_at {self.last_seen_at.isoformat()} precedes "
                f"first_seen_at {self.first_seen_at.isoformat()}"
            )
        return self


class CautionSignal(BaseModel):
    """Something that can only make the system more careful (I7, I15).

    Read `min_verdict` as a floor: the policy engine takes the maximum of every verdict it
    holds, so a signal can push the result up and has no way to pull it down. `ALLOW` is
    excluded at the type level, which is why there is no runtime check for it here.
    """

    model_config = _FROZEN

    kind: SignalKind
    min_verdict: Literal[V.REQUIRE_APPROVAL, V.ESCALATE]
    reason: Annotated[str, Field(min_length=1, max_length=MAX_SIGNAL_REASON_CHARS)]
    source_ref: str


class CaseOutcome(BaseModel):
    """Sent to CaseOutcomeObserver when a case enters a terminal state (HOOK C).

    `used_pattern_ids` is what stops circular confirmation: a pattern that was shown to the
    investigator on this case is named here, so Phase 7 can refuse to count the case as
    independent evidence for the pattern that helped produce it.
    """

    model_config = _FROZEN

    id: OutcomeId
    case_id: CaseId
    state: CaseState
    resolved: bool
    facts: CaseFacts
    assessment_id: AssessmentId | None = None
    credited: Money | None = None
    used_pattern_ids: tuple[PatternId, ...] = ()
    closed_at: datetime
    reopen_count: Annotated[int, Field(ge=0)] = 0

    @field_validator("closed_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "closed_at")

    @model_validator(mode="after")
    def _check_terminal(self) -> Self:
        if self.state not in TERMINAL_STATES:
            raise DomainValidationError(
                f"a case outcome needs a terminal state, not {self.state.value}"
            )
        if self.resolved != (self.state in RESOLVED_STATES):
            raise DomainValidationError(
                f"resolved={self.resolved} contradicts state {self.state.value}; "
                "only RESOLVED_CREDITED and RESOLVED_UPHELD are resolved"
            )
        return self
