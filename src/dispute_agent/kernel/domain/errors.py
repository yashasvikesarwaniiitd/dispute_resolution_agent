"""Typed errors (kernel-interfaces.md section 5).

Every error carries a stable `code` for logs and events. CLAUDE.md section 7 forbids bare
`except`, so the hierarchy matters: a caller catches the narrowest class that it can
actually handle. The port errors below are the contract every adapter translates its
library's exceptions into, which is what lets the kernel stay free of provider details.

The distinction that matters most is TransientPortError versus AmbiguousOutcome: the first
certainly did not take effect and is safe to retry, the second may have, and retrying it
could duplicate a side effect (I2).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.enums import CaseState, Trigger
    from dispute_agent.kernel.domain.ids import CaseId, DedupeKey, InboxId


class KernelError(Exception):
    """Base. Every error carries a stable `code` string for logs and events."""

    code: ClassVar[str] = "kernel_error"


class DomainValidationError(KernelError):
    """A domain rule was broken.

    Note what this deliberately does not inherit from. pydantic wraps a `ValueError` raised
    inside a validator into its own `ValidationError`; because this is not one, it travels
    out of a model validator unchanged, which is what kernel-interfaces.md section 4.3 means
    by "model validators raise DomainValidationError".

    So constructing a domain model can fail two ways, and the difference is the useful part:
    `ValidationError` means the input was the wrong *shape* — a missing field, a string where
    a Decimal belongs, a value outside a Literal. `DomainValidationError` means the shape was
    right and the *rule* was broken — a naive datetime, a resolved outcome in a state that
    settles nothing. The first is a caller bug; the second is this system saying no.
    """

    code: ClassVar[str] = "domain_validation"


class ConfigError(KernelError):
    """Startup only; the app refuses to start (CLAUDE.md section 6: fail fast at startup)."""

    code: ClassVar[str] = "config"


class IllegalTransition(KernelError):
    code: ClassVar[str] = "illegal_transition"

    def __init__(self, state: CaseState | None, trigger: Trigger, reason: str) -> None:
        self.state = state
        self.trigger = trigger
        self.reason = reason
        super().__init__(f"{trigger} is not legal from {state}: {reason}")


class GuardFailed(IllegalTransition):
    """The trigger exists from this state, but no guard set held."""

    code: ClassVar[str] = "guard_failed"


class ConcurrencyConflict(KernelError):
    """Optimistic version mismatch (ADR 0008). Retried, never mapped to a review reason."""

    code: ClassVar[str] = "concurrency_conflict"

    def __init__(self, case_id: CaseId, expected_version: int) -> None:
        self.case_id = case_id
        self.expected_version = expected_version
        super().__init__(f"case {case_id} is not at version {expected_version}")


class NotFound(KernelError):
    code: ClassVar[str] = "not_found"


class DuplicateEvent(KernelError):
    """The inbox already holds this dedupe key (ADR 0006)."""

    code: ClassVar[str] = "duplicate_event"

    def __init__(self, dedupe_key: DedupeKey, existing_inbox_id: InboxId) -> None:
        self.dedupe_key = dedupe_key
        self.existing_inbox_id = existing_inbox_id
        super().__init__(f"{dedupe_key} was already accepted as {existing_inbox_id}")


class ModuleUnavailable(KernelError):
    """A needed module is disabled or unreachable. The orchestrator turns this into
    NEEDS_HUMAN_REVIEW with reason module_unavailable:<module> (I6). Never skip silently."""

    code: ClassVar[str] = "module_unavailable"

    def __init__(self, module: str) -> None:
        self.module = module
        super().__init__(module)


class GuardrailBlocked(KernelError):
    """A provider content check blocked the input. Advisory elsewhere, but a block
    sends the case to a human (I15)."""

    code: ClassVar[str] = "guardrail_blocked"


class PolicyRefused(KernelError):
    """The executor refused because a fresh policy evaluation or approval check failed (I2, I13)."""

    code: ClassVar[str] = "policy_refused"

    def __init__(self, verdict: object | None, reason: str) -> None:
        self.verdict = verdict
        self.reason = reason
        super().__init__(reason)


class KillSwitchEngaged(KernelError):
    """Checked inside the executor, not only at the entry point (I10)."""

    code: ClassVar[str] = "kill_switch_active"


# --- errors an adapter may raise from a port method -------------------------


class PortError(KernelError):
    code: ClassVar[str] = "port_error"


class TransientPortError(PortError):
    """The request certainly did not take effect (connection refused, 429). Safe to retry."""

    code: ClassVar[str] = "transient"

    def __init__(self, detail: str, retry_after_s: float | None = None) -> None:
        self.detail = detail
        self.retry_after_s = retry_after_s
        super().__init__(detail)


class AmbiguousOutcome(PortError):
    """The request may or may not have taken effect (timeout after send, 5xx on a write).

    Never retried blindly: that is how a second email or a second credit happens. The
    executor looks the action up by its idempotency key instead (I2, ADR 0007).
    """

    code: ClassVar[str] = "ambiguous"

    def __init__(self, detail: str, external_ref: str | None = None) -> None:
        self.detail = detail
        self.external_ref = external_ref
        super().__init__(detail)


class PermanentPortError(PortError):
    """Retrying cannot help (400 validation, 404 on a write target, 422)."""

    code: ClassVar[str] = "permanent"

    def __init__(self, detail: str, status: int | None = None) -> None:
        self.detail = detail
        self.status = status
        super().__init__(detail)


class AuthPortError(PermanentPortError):
    """401 or 403. Also logged at ERROR level."""

    code: ClassVar[str] = "auth"


class ExternalNotFound(PortError):
    """A read-back target is not (yet) visible. Not proof of absence."""

    code: ClassVar[str] = "external_not_found"


class InvalidModelOutput(PortError):
    """Model output failed schema or proposal validation, after the one permitted retry.

    `usage` carries what the failed attempts cost, when the gateway knows it: a model that
    answered badly was still paid for, and the spending cap must see that.
    """

    code: ClassVar[str] = "invalid_model_output"

    def __init__(self, detail: str, usage: object | None = None) -> None:
        self.detail = detail
        self.usage = usage
        super().__init__(detail)


class BudgetExceeded(PortError):
    code: ClassVar[str] = "budget_exceeded"

    def __init__(self, kind: Literal["llm_budget_exceeded", "credit_cap_reached"]) -> None:
        self.kind = kind
        super().__init__(kind)


class AgentDrift(PortError):
    """The deployed agent no longer matches the repo prompt. Never fails over (F01)."""

    code: ClassVar[str] = "agent_drift"
