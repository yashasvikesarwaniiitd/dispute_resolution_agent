"""The one guarded path to a model (ADR 0017, I14, D18).

`BudgetedModelGateway` wraps the real gateway and is the only gateway the app hands to a
reasoner. Before each call it asks three questions, and any "no" means the call does not
happen - the case goes to a person with `llm_budget_exceeded`:

1. **Has this case had its share?** A per-case cap, so one confusing dispute cannot loop
   the model all afternoon.
2. **Is the money there?** A lifetime cap on the route's spend, set below the Azure credit,
   so the credit is never the thing that runs out first.
3. **Is the credit window still open?** After `credit_expires_on`, calls stop unless the
   owner has explicitly allowed paid usage. Running out of free credit should stop the
   prototype, not quietly start a bill.

After each call, success or not, it writes one row to the call log: route, model, prompt,
tokens, cost and how it ended. Never the text (I14).

**What it does not do yet** (Phase 4+): failover to the Haiku route. With no secondary
route, a primary that is down raises and the investigate step falls back to rules.

**Where the cap can overshoot.** The check is before the call and the cost is known only
after, so the last call can take spend past the cap by the price of one call - about a
third of a US cent at the prototype's prices and token limit. The cap is set $50 below the
credit, so this cannot reach the credit's edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    BudgetExceeded,
    GuardrailBlocked,
    InvalidModelOutput,
    KernelError,
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
    """Implements ModelGateway, around exactly one primary route."""

    primary: ModelGateway
    route: str
    ledger: ModelUsageLedger
    limits: LlmConfig
    money: LlmPrimaryConfig
    clock: Clock
    ids: IdGenerator

    def complete(self, req: ModelRequest) -> ModelResponse:
        refusal = self._refusal(req)
        if refusal is not None:
            self._record(req, "budget", _NO_USAGE, model_id="none", error_code=refusal)
            raise BudgetExceeded("llm_budget_exceeded")

        try:
            response = self.primary.complete(req)
        except InvalidModelOutput as invalid:
            usage = invalid.usage if isinstance(invalid.usage, ModelUsage) else _NO_USAGE
            self._record(req, "invalid_output", usage, error_code=invalid.code)
            raise
        except (GuardrailBlocked, AgentDrift) as stopped:
            self._record(req, "blocked", _NO_USAGE, error_code=stopped.code)
            raise
        except KernelError as failed:
            self._record(req, "error", _NO_USAGE, error_code=failed.code)
            raise
        self._record(req, "ok", response.usage, model_id=response.model_id)
        return response

    def _refusal(self, req: ModelRequest) -> str | None:
        """Why this call must not happen, or None. Checked in order of cheapest to read."""
        expires = self.money.credit_expires_on
        if (
            expires is not None
            and not self.money.allow_paid_usage
            and self.clock.now().date() > expires
        ):
            return "credit_window_ended"
        if (
            req.case_id is not None
            and self.ledger.calls_for_case(req.case_id) >= self.limits.max_calls_per_case
        ):
            return "max_calls_per_case"
        if self.ledger.spend_usd(self.route) >= self.money.spend_cap_usd:
            return "spend_cap_reached"
        return None

    def _record(
        self,
        req: ModelRequest,
        status: CallStatus,
        usage: ModelUsage,
        *,
        model_id: str = "unknown",
        error_code: str | None = None,
    ) -> None:
        self.ledger.record(
            ModelCallRecord(
                id=self.ids.new_id("mcl"),
                case_id=req.case_id,
                purpose=req.purpose,
                route=self.route,
                model_id=model_id,
                prompt_name=req.prompt.name,
                prompt_version=req.prompt.version,
                prompt_hash=str(req.prompt.hash),
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=usage.latency_ms,
                cost_usd=usage.cost_usd,
                status=status,
                error_code=error_code,
                created_at=self.clock.now(),
            )
        )


__all__ = ["BudgetedModelGateway"]
