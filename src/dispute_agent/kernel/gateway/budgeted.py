"""The one guarded path to a model: budgets and failover (ADR 0017, F01, M01-M06, I14, D17).

`BudgetedModelGateway` wraps the primary route (Foundry) and, optionally, the secondary
(Anthropic API, Haiku only). It is the only gateway the app hands to a reasoner.

**Before a call** (policy-rules.md section 6):

- M01 calls per case - reached: stop (`llm_budget_exceeded`). Never fails over.
- M03 primary spend cap - reached: stop (`credit_cap_reached`). Never fails over: a cap is a
  limit the owner set on purpose, and spending on another provider would defeat it (D17).
- M04 credit window ended and paid usage off: the primary is *unavailable* - the owner
  planned for the free credit to stop - so this may fail over; with no secondary it stops
  (`credit_window_ended`).

**After a primary failure (F01):** fail over only when the primary was unavailable
(TransientPortError after its own retries, or ModuleUnavailable), and only if the secondary
is configured and its own cap (M05) has room. Guardrail blocks, drift, invalid output and
auth or configuration faults never fail over - each is something a person must see. If the
secondary is also unavailable, that error goes up and the investigate step lets the rules
baseline decide the clear cases.

**Recording:** every call - primary, secondary, refused - writes one row to the model-call
log with its route, cost and outcome (never content), and a failover row names the route it
came from. `failovers` keeps the reasons for the step to report.

**Where the cap can overshoot:** the check is before the call and the cost is known after,
so the last call on a route can pass its cap by one call's price.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    BudgetExceeded,
    GuardrailBlocked,
    InvalidModelOutput,
    KernelError,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.kernel.domain.model_calls import CallStatus, ModelCallRecord
from dispute_agent.kernel.domain.reasoning import ModelUsage

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.kernel.policy.config import LlmConfig, LlmPrimaryConfig
    from dispute_agent.kernel.ports import Clock, IdGenerator, ModelGateway
    from dispute_agent.kernel.ports.model_usage import ModelUsageLedger

_NO_USAGE = ModelUsage(input_tokens=0, output_tokens=0, latency_ms=0, cost_usd=Decimal(0))


@dataclass
class BudgetedModelGateway:
    """Implements ModelGateway."""

    primary: ModelGateway
    route: str
    ledger: ModelUsageLedger
    limits: LlmConfig
    money: LlmPrimaryConfig
    clock: Clock
    ids: IdGenerator
    secondary: ModelGateway | None = None
    secondary_route: str = "anthropic_direct"
    secondary_cap_usd: Decimal = Decimal("10.00")
    failovers: list[str] = field(default_factory=list)

    def complete(self, req: ModelRequest) -> ModelResponse:
        # M01, before anything else: it binds every route.
        if req.case_id is not None and (
            self.ledger.calls_for_case(req.case_id) >= self.limits.max_calls_per_case
        ):
            self._record(req, self.route, "budget", _NO_USAGE, error_code="max_calls_per_case")
            raise BudgetExceeded("llm_budget_exceeded")

        # M03: never fails over.
        if self.ledger.spend_usd(self.route) >= self.money.spend_cap_usd:
            self._record(req, self.route, "budget", _NO_USAGE, error_code="spend_cap_reached")
            raise BudgetExceeded("credit_cap_reached")

        # M04: an availability event, so it may fail over.
        if self._credit_window_ended():
            self._record(req, self.route, "budget", _NO_USAGE, error_code="credit_window_ended")
            if self.secondary is None:
                raise BudgetExceeded("credit_window_ended")
            return self._fail_over(req, "credit_window_ended")

        try:
            return self._call(self.primary, self.route, req)
        except (TransientPortError, ModuleUnavailable) as unavailable:
            if self.secondary is None:
                raise
            return self._fail_over(req, unavailable.code)

    # -- routes ----------------------------------------------------------------------------------

    def _fail_over(self, req: ModelRequest, reason: str) -> ModelResponse:
        assert self.secondary is not None
        # M05: the secondary's own cap; reaching it is a stop, never a further route (D42).
        if self.ledger.spend_usd(self.secondary_route) >= self.secondary_cap_usd:
            self._record(
                req, self.secondary_route, "budget", _NO_USAGE, error_code="spend_cap_reached"
            )
            raise BudgetExceeded("credit_cap_reached")
        self.failovers.append(f"{self.route}->{self.secondary_route}: {reason}")
        return self._call(self.secondary, self.secondary_route, req, failover_from=self.route)

    def _call(
        self,
        gateway: ModelGateway,
        route: str,
        req: ModelRequest,
        *,
        failover_from: str | None = None,
    ) -> ModelResponse:
        try:
            response = gateway.complete(req)
        except InvalidModelOutput as invalid:
            self._record(
                req, route, "invalid_output", _usage_of(invalid), invalid.code, failover_from
            )
            raise
        except (GuardrailBlocked, AgentDrift) as stopped:
            self._record(req, route, "blocked", _usage_of(stopped), stopped.code, failover_from)
            raise
        except KernelError as failed:
            self._record(req, route, "error", _usage_of(failed), failed.code, failover_from)
            raise
        except Exception as unexpected:
            # A bug, or a provider failure no adapter mapped: still recorded, so it counts.
            self._record(
                req, route, "error", _NO_USAGE, type(unexpected).__name__[:60], failover_from
            )
            raise
        self._record(
            req, route, "ok", response.usage, None, failover_from, model_id=response.model_id
        )
        return response

    def _credit_window_ended(self) -> bool:
        expires = self.money.credit_expires_on
        return (
            expires is not None
            and not self.money.allow_paid_usage
            and self.clock.now().date() > expires
        )

    def _record(
        self,
        req: ModelRequest,
        route: str,
        status: CallStatus,
        usage: ModelUsage,
        error_code: str | None = None,
        failover_from: str | None = None,
        *,
        model_id: str = "unknown",
    ) -> None:
        self.ledger.record(
            ModelCallRecord(
                id=self.ids.new_id("mcl"),
                case_id=req.case_id,
                purpose=req.purpose,
                route=route,
                model_id=model_id
                if status == "ok"
                else ("none" if status == "budget" else model_id),
                prompt_name=req.prompt.name,
                prompt_version=req.prompt.version,
                prompt_hash=str(req.prompt.hash),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=usage.latency_ms,
                cost_usd=usage.cost_usd,
                status=status,
                error_code=error_code,
                failover_from=failover_from,
                created_at=self.clock.now(),
            )
        )


def _usage_of(failed: KernelError) -> ModelUsage:
    return failed.usage if isinstance(failed.usage, ModelUsage) else _NO_USAGE


__all__ = ["BudgetedModelGateway"]
