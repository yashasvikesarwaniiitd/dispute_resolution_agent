"""The kernel-facing part of `config/policy.yaml` (kernel-interfaces.md 7, config.md 3).

Frozen, loaded once at startup. Two fields are deliberately not configurable in the way they
look:

`credits.require_approval` is `Literal[True]`. It is in the file so a reader can see the rule
written down, and it is a type error to set it to anything else — I3 is not a setting.

`messages.review_required_kinds` and `auto_send_kinds` must be disjoint and must together
cover every message kind. A kind in neither would send automatically by accident, which is
exactly the hole I13 exists to close, so the model refuses it rather than defaulting.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dispute_agent.kernel.domain.enums import MessageKind, VerdictKind
from dispute_agent.kernel.domain.errors import ConfigError

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class TimersConfig(BaseModel):
    model_config = _FROZEN

    reminder_after_hours: int = 48
    decision_after_hours: int = 72
    reopen_window_days: int = 7
    ops_timeout_hours: int = 120
    evidence_retry_minutes: int = 15
    evidence_max_retries: int = 3  # D41


class ContactConfig(BaseModel):
    model_config = _FROZEN

    max_messages_per_case: int = 3
    max_messages_per_day: int = 1
    day_boundary_tz: str = "Asia/Kolkata"
    max_clarifications_per_case: int = 1  # D19: one follow-up, then a person

    @field_validator("day_boundary_tz")
    @classmethod
    def _zone_resolves(cls, value: str) -> str:
        """Fail at start-up, not when a message is about to go out.

        "One message a day" means a day where the seller is, so the zone is a product
        decision. But an unresolvable zone - a typo, or a machine with no IANA database -
        would otherwise surface as an exception inside policy evaluation, which reads as a
        broken case rather than as broken configuration. Worse, a silent fall back to UTC
        would move the boundary by five and a half hours and let a seller be written to
        twice in their evening.
        """
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as unknown:
            raise ConfigError(
                f"contact.day_boundary_tz {value!r} is not a zone this machine knows: {unknown}"
            ) from unknown
        return value


class MessagesConfig(BaseModel):
    model_config = _FROZEN

    review_required_kinds: frozenset[MessageKind] = frozenset(MessageKind)
    auto_send_kinds: frozenset[MessageKind] = frozenset()
    #: Test addresses only (I11). Resolved from TEST_SELLER_EMAIL by the settings loader.
    allowed_recipients: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _every_kind_is_decided(self) -> Self:
        overlap = self.review_required_kinds & self.auto_send_kinds
        if overlap:
            raise ConfigError(
                f"message kinds {sorted(k.value for k in overlap)} are both reviewed and "
                "auto-sent; one of the two has to win and the file does not say which"
            )
        undecided = set(MessageKind) - self.review_required_kinds - self.auto_send_kinds
        if undecided:
            raise ConfigError(
                f"message kinds {sorted(k.value for k in undecided)} are in neither list; "
                "an undecided kind would send without review (I13)"
            )
        return self


class CreditsConfig(BaseModel):
    model_config = _FROZEN

    #: Not a setting. In the file so the rule is visible, Literal[True] so it cannot be
    #: switched off by editing YAML (I3).
    require_approval: Literal[True] = True
    reason_codes: frozenset[str] = frozenset(
        {"calculation_error", "remeasurement_lower", "duplicate_charge"}
    )


class ApprovalsConfig(BaseModel):
    model_config = _FROZEN

    expire_after_hours: int = 72
    max_rejections_per_case: int = 2
    approver_ids: frozenset[str] = frozenset({"human:owner"})


class CasesConfig(BaseModel):
    model_config = _FROZEN

    max_agent_reopens: int = 2
    max_steps_per_advance: int = 12


class BaselineConfig(BaseModel):
    """The rules-only baseline's one knob (config.md, agent-design.md section 5).

    A measured size within this percentage of the declared size counts as agreeing, so B8
    upholds; beyond it, B7 asks the seller for a photo. It is configuration rather than a
    constant because it is a judgement about scanners and packing, not a fact.
    """

    model_config = _FROZEN

    tolerance_pct: int = 5


class PatternsConfig(BaseModel):
    model_config = _FROZEN

    min_related_cases: int = 3
    window_days: int = 14
    #: I7: a pattern may raise the verdict and never lower it, so ALLOW is not an option.
    match_min_verdict: Literal[VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE] = (
        VerdictKind.REQUIRE_APPROVAL
    )


class LlmConfig(BaseModel):
    """Per-case model-call limits (config.md section 3, ADR 0017)."""

    model_config = _FROZEN

    max_calls_per_case: int = Field(default=6, gt=0)
    #: Reasoning models spend part of this thinking before they answer (foundry.md 6).
    max_output_tokens: int = Field(default=4000, gt=0)
    #: Per model call. Reasoning models can take tens of seconds (foundry.md section 7).
    call_timeout_s: int = Field(default=120, gt=0)


class LlmPrimaryConfig(BaseModel):
    """The primary route's money limits (D18, ADR 0029).

    `allow_paid_usage` is the switch between "spend only the free credit" and "spend real
    money". Off by default and only turned on by the owner (LLM_ALLOW_PAID_USAGE), so the
    end of the credit window stops model calls rather than starting a bill.
    """

    model_config = _FROZEN

    spend_cap_usd: Decimal = Field(default=Decimal("150.00"), gt=0)
    credit_expires_on: date | None = None
    allow_paid_usage: bool = False


class LlmSecondaryConfig(BaseModel):
    """The Haiku route's limits (D17, M05, M06). Its cap is its own: never shared, never
    crossed by failover."""

    model_config = _FROZEN

    allowed_models: str = "claude-haiku-*"
    spend_cap_usd: Decimal = Field(default=Decimal("10.00"), gt=0)


class PolicyConfig(BaseModel):
    """The kernel-facing part of config/policy.yaml."""

    model_config = _FROZEN

    timers: TimersConfig = Field(default_factory=TimersConfig)
    contact: ContactConfig = Field(default_factory=ContactConfig)
    messages: MessagesConfig = Field(default_factory=MessagesConfig)
    credits: CreditsConfig = Field(default_factory=CreditsConfig)
    approvals: ApprovalsConfig = Field(default_factory=ApprovalsConfig)
    cases: CasesConfig = Field(default_factory=CasesConfig)
    patterns: PatternsConfig = Field(default_factory=PatternsConfig)
    baseline: BaselineConfig = Field(default_factory=BaselineConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    llm_primary: LlmPrimaryConfig = Field(default_factory=LlmPrimaryConfig)
    llm_secondary: LlmSecondaryConfig = Field(default_factory=LlmSecondaryConfig)

    def rules_version(self) -> str:
        """A hash of the effective configuration, recorded on every verdict.

        Without it, "why did this case need approval?" is unanswerable after someone edits
        the file: the verdict would name rules whose thresholds have since changed.
        """
        from dispute_agent.kernel.domain.hashing import payload_hash

        return payload_hash(self.model_dump(mode="json"))
