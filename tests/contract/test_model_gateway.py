"""Contract: ModelGateway (kernel-interfaces.md 6.4, foundry.md, I14).

The only path to a model, so this suite is where every route has to prove it behaves the
same. A Foundry agent and a Haiku call have almost nothing in common underneath; what the
kernel is entitled to assume is that both validate output against the prompt's schema, both
retry exactly once, and both raise the same typed errors rather than their own.

`anthropic_direct` is restricted by code to Haiku models (I14), so the suite checks that the
restriction is in the adapter rather than in whatever configuration happens to be loaded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import ModelGateway

ADAPTERS = [
    pending("fake", phase="Phase 4 (card P4-03)"),
    pending("anthropic_direct", phase="Phase 4 (card P4-07)", integration=True),
    pending("foundry_agent_service", phase="Phase 4b (card P4B-02)", integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def gateway(request: pytest.FixtureRequest) -> ModelGateway:
    return cast("ModelGateway", build_or_skip(request.param))


class TestOutput:
    def test_a_valid_response_is_parsed_against_the_prompt_schema(
        self, gateway: ModelGateway
    ) -> None: ...

    def test_invalid_json_is_retried_once_then_raises_invalid_model_output(
        self, gateway: ModelGateway
    ) -> None:
        """TC-MOD-01: twice invalid escalates. No fallback (FLOW section 7b)."""

    def test_output_with_extra_properties_is_refused(self, gateway: ModelGateway) -> None: ...

    def test_usage_is_reported_with_cost_as_a_decimal(self, gateway: ModelGateway) -> None:
        """I9 again: spend caps are compared against these numbers."""


class TestErrors:
    def test_a_guardrail_block_raises_guardrail_blocked(self, gateway: ModelGateway) -> None:
        """I15: a block sends the case to a human, it does not retry into success."""

    def test_a_rate_limit_raises_transient_with_retry_after(
        self, gateway: ModelGateway
    ) -> None: ...

    def test_an_auth_failure_raises_permanent_not_transient(self, gateway: ModelGateway) -> None:
        """Retrying a bad credential is how a test account gets locked."""


class TestSafety:
    def test_no_sampling_parameters_are_sent(self, gateway: ModelGateway) -> None:
        """foundry.md section 6: the adapters never send them, so runs stay comparable."""

    def test_the_haiku_route_refuses_a_non_haiku_model(self, gateway: ModelGateway) -> None:
        """I14: restricted by code, not by whichever config file is loaded."""

    def test_prompt_and_response_content_never_reaches_telemetry(
        self, gateway: ModelGateway
    ) -> None:
        """I14, unless telemetry.capture_content and APP_ENV=test."""
