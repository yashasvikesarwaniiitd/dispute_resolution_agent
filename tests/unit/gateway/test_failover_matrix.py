"""F01, exhaustively (P4b-06, policy-rules.md section 6, D17, D42).

One test per row of the table: what the primary did, whether the secondary is called, and
what the case is told. A wrong cell here silently spends the owner's money on another
provider - or hides a fault that should have been seen - so every cause has its own test.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
from dispute_agent.kernel.domain.errors import (
    AgentDrift,
    AuthPortError,
    BudgetExceeded,
    GuardrailBlocked,
    InvalidModelOutput,
    KernelError,
    ModuleUnavailable,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.reasoning import ModelResponse, ModelUsage
from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway
from dispute_agent.kernel.policy.config import LlmConfig, LlmPrimaryConfig
from tests.model_fixtures import request, valid_answer

if TYPE_CHECKING:
    from pathlib import Path

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
CASE = CaseId("case_01HQ")


class Route:
    """A route that raises `fault` or answers, and counts its calls."""

    def __init__(self, name: str, fault: KernelError | None = None) -> None:
        self.name, self.fault, self.calls = name, fault, 0

    def complete(self, req: Any) -> ModelResponse:
        self.calls += 1
        if self.fault is not None:
            raise self.fault
        return ModelResponse(
            output=valid_answer(),
            model_id=f"{self.name}-model",
            route=self.name,
            usage=ModelUsage(
                input_tokens=10, output_tokens=10, latency_ms=1, cost_usd=Decimal("0.001")
            ),
        )


def guard(
    tmp_path: Path,
    primary: Route,
    secondary: Route | None,
    *,
    expires: date | None = None,
    primary_cap: str = "150.00",
    secondary_cap: str = "10.00",
) -> tuple[BudgetedModelGateway, Any]:
    ledger = open_model_call_log(tmp_path / "calls.sqlite3")
    return (
        BudgetedModelGateway(
            primary=primary,
            route="foundry_agent_service",
            ledger=ledger,
            limits=LlmConfig(max_calls_per_case=6),
            money=LlmPrimaryConfig(spend_cap_usd=Decimal(primary_cap), credit_expires_on=expires),
            clock=FakeClock(NOW),
            ids=FakeIdGenerator(),
            secondary=secondary,
            secondary_cap_usd=Decimal(secondary_cap),
        ),
        ledger,
    )


def req() -> Any:
    return request().model_copy(update={"case_id": CASE})


#: The rows of F01 where the primary failed and failover *is* allowed.
FAILS_OVER = [
    pytest.param(TransientPortError("unavailable"), id="unavailable"),
    pytest.param(TransientPortError("rate limited", retry_after_s=2), id="rate_limited"),
    pytest.param(TransientPortError("quota exceeded"), id="quota_exceeded"),
    pytest.param(ModuleUnavailable("model_gateway"), id="module_unavailable"),
]

#: The rows where it is *not*, and the error the case sees instead.
NEVER_FAILS_OVER = [
    pytest.param(GuardrailBlocked("blocked"), GuardrailBlocked, id="guardrail_blocked"),
    pytest.param(AgentDrift("drift"), AgentDrift, id="agent_drift"),
    pytest.param(InvalidModelOutput("bad"), InvalidModelOutput, id="invalid_model_output"),
    pytest.param(AuthPortError("auth", status=401), AuthPortError, id="auth"),
    pytest.param(PermanentPortError("gone", status=410), PermanentPortError, id="permanent"),
]


class TestTheRowsThatFailOver:
    @pytest.mark.parametrize("fault", FAILS_OVER)
    def test_an_unavailable_primary_is_served_by_the_secondary(
        self, tmp_path: Path, fault: KernelError
    ) -> None:
        primary, secondary = Route("foundry", fault), Route("haiku")
        gateway, _ = guard(tmp_path, primary, secondary)
        response = gateway.complete(req())
        assert response.route == "haiku"
        assert (primary.calls, secondary.calls) == (1, 1)
        assert gateway.failovers

    def test_s23a_the_failover_is_recorded_with_its_origin(self, tmp_path: Path) -> None:
        gateway, ledger = guard(tmp_path, Route("f", TransientPortError("x")), Route("h"))
        gateway.complete(req())
        assert ledger.spend_usd("anthropic_direct") > 0

    def test_an_ended_credit_window_fails_over_without_calling_the_primary(
        self, tmp_path: Path
    ) -> None:
        primary, secondary = Route("foundry"), Route("haiku")
        gateway, _ = guard(tmp_path, primary, secondary, expires=date(2026, 11, 1))
        assert gateway.complete(req()).route == "haiku"
        assert primary.calls == 0


class TestTheRowsThatNeverFailOver:
    @pytest.mark.parametrize(("fault", "raised"), NEVER_FAILS_OVER)
    def test_the_secondary_is_never_called(
        self, tmp_path: Path, fault: KernelError, raised: type[Exception]
    ) -> None:
        secondary = Route("haiku")
        gateway, _ = guard(tmp_path, Route("foundry", fault), secondary)
        with pytest.raises(raised):
            gateway.complete(req())
        assert secondary.calls == 0

    def test_m03_a_reached_primary_cap_stops_and_never_spends_elsewhere(
        self, tmp_path: Path
    ) -> None:
        """D17: a cap is a limit the owner set on purpose."""
        primary, secondary = Route("foundry"), Route("haiku")
        gateway, _ = guard(tmp_path, primary, secondary, primary_cap="0.0001")
        gateway.complete(req())  # spends 0.001, past the cap
        with pytest.raises(BudgetExceeded) as raised:
            gateway.complete(req())
        assert raised.value.kind == "credit_cap_reached"
        assert secondary.calls == 0

    def test_m01_calls_per_case_stops_every_route(self, tmp_path: Path) -> None:
        """Every provider call counts, the failed primary one included: both were real."""
        primary, secondary = Route("foundry", TransientPortError("x")), Route("haiku")
        gateway, _ = guard(tmp_path, primary, secondary)
        with pytest.raises(BudgetExceeded) as raised:
            for _ in range(10):
                gateway.complete(req())
        assert raised.value.kind == "llm_budget_exceeded"
        assert primary.calls + secondary.calls <= 6

    def test_m05_a_reached_secondary_cap_stops_with_credit_cap_reached(
        self, tmp_path: Path
    ) -> None:
        """D42: no route left, and never past a cap."""
        primary, secondary = Route("foundry", TransientPortError("x")), Route("haiku")
        gateway, _ = guard(tmp_path, primary, secondary, secondary_cap="0.0001")
        gateway.complete(req())  # the secondary spends past its cap
        with pytest.raises(BudgetExceeded) as raised:
            gateway.complete(req())
        assert raised.value.kind == "credit_cap_reached"
        assert secondary.calls == 1


class TestWhenNoRouteIsLeft:
    def test_the_secondary_also_unavailable_goes_up_for_the_rules_baseline(
        self, tmp_path: Path
    ) -> None:
        """D42: availability on both routes -> the investigate step lets the rules decide."""
        primary = Route("foundry", TransientPortError("x"))
        secondary = Route("haiku", TransientPortError("y"))
        gateway, _ = guard(tmp_path, primary, secondary)
        with pytest.raises(TransientPortError):
            gateway.complete(req())

    def test_without_a_secondary_an_unavailable_primary_goes_up(self, tmp_path: Path) -> None:
        gateway, _ = guard(tmp_path, Route("foundry", TransientPortError("x")), None)
        with pytest.raises(TransientPortError):
            gateway.complete(req())

    def test_m04_without_a_secondary_stops_with_credit_window_ended(self, tmp_path: Path) -> None:
        gateway, _ = guard(tmp_path, Route("foundry"), None, expires=date(2026, 11, 1))
        with pytest.raises(BudgetExceeded) as raised:
            gateway.complete(req())
        assert raised.value.kind == "credit_window_ended"

    def test_the_caps_are_counted_per_route(self, tmp_path: Path) -> None:
        gateway, ledger = guard(tmp_path, Route("f", TransientPortError("x")), Route("h"))
        gateway.complete(req())
        assert ledger.spend_usd("foundry_agent_service") == 0
        assert ledger.spend_usd("anthropic_direct") > 0
