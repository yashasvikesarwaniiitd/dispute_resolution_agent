"""Contract: ModelGateway (kernel-interfaces.md 6.4, foundry.md, I14).

The only path to a model, so this suite is where every route has to prove it behaves the
same. A Foundry agent and a Haiku call have almost nothing in common underneath; what the
kernel is entitled to assume is that both validate output against the prompt's schema, both
retry exactly once, and both raise the same typed errors rather than their own.

Each adapter supplies a harness that builds a gateway whose provider will answer in a given
way (`answers=[...]`, each a JSON-able dict, raw text, or a fault name). Assertions are made
through the port alone; a harness that cannot arrange a condition skips that case by name.

`anthropic_direct` is restricted by code to Haiku models (I14), so the suite checks that the
restriction is in the adapter rather than in whatever configuration happens to be loaded.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Protocol

import pytest
import structlog

from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    GuardrailBlocked,
    InvalidModelOutput,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from tests.contract.conftest import build_or_skip, params, pending, ready
from tests.contract.harness import UnsupportedError, arrange
from tests.foundry_stub import deployed_for
from tests.model_fixtures import SECRET_MARKER, request, valid_answer


class GatewayHarness(Protocol):
    name: str

    def gateway(self, answers: list[Any]) -> Any:
        """A gateway whose provider gives these answers in order."""
        ...

    def provider_calls(self, gateway: Any) -> int: ...

    def sent_options(self, gateway: Any) -> list[dict[str, Any]]:
        """The options each provider call was sent with. UnsupportedError if not visible."""
        ...

    def gateway_for_model(self, model: str) -> Any:
        """A gateway configured for `model`. UnsupportedError where there is no choice."""
        ...


class FakeHarness:
    name = "fake"

    def gateway(self, answers: list[Any]) -> FakeModelGateway:
        return FakeModelGateway(script=list(answers))

    def provider_calls(self, gateway: FakeModelGateway) -> int:
        return gateway.provider_calls()

    def sent_options(self, gateway: FakeModelGateway) -> list[dict[str, Any]]:
        raise UnsupportedError("the fake sends nothing to a provider")

    def gateway_for_model(self, model: str) -> FakeModelGateway:
        raise UnsupportedError("the fake has no model choice")


class FoundryStubHarness:
    """The real Foundry gateway over a stand-in for Azure (tests/foundry_stub.py).

    Everything above the SDK runs for real: drift check, retries, answer checking, costing.
    The live version of this adapter runs under the integration marker in Phase 4+.
    """

    name = "foundry_agent_service"

    def gateway(self, answers: list[Any]) -> Any:
        built, stub = deployed_for(request().prompt, answers)
        built.stub = stub  # type: ignore[attr-defined]
        return built

    def provider_calls(self, gateway: Any) -> int:
        return len(gateway.stub.calls)

    def sent_options(self, gateway: Any) -> list[dict[str, Any]]:
        return list(gateway.stub.calls)

    def gateway_for_model(self, model: str) -> Any:
        raise UnsupportedError("the Foundry route's model is the deployment, not a choice")


ADAPTERS = [
    ready("fake", FakeHarness),
    ready("foundry_agent_service (stub Azure)", FoundryStubHarness),
    pending("anthropic_direct", phase="Phase 4+ (card P4-07)", integration=True),
]


@pytest.fixture(params=params(ADAPTERS))
def harness(request: pytest.FixtureRequest) -> GatewayHarness:
    built: GatewayHarness = build_or_skip(request.param)
    return built


class TestOutput:
    def test_a_valid_response_is_parsed_against_the_prompt_schema(
        self, harness: GatewayHarness
    ) -> None:
        gateway = harness.gateway([valid_answer()])
        response = gateway.complete(request())
        assert response.output["action"] == "request_seller_evidence"
        assert harness.provider_calls(gateway) == 1

    def test_invalid_json_is_retried_once_then_raises_invalid_model_output(
        self, harness: GatewayHarness
    ) -> None:
        """TC-MOD-01: twice invalid escalates. No fallback (FLOW section 7b)."""
        gateway = harness.gateway(["not json", "```json\n{}\n```", valid_answer()])
        with pytest.raises(InvalidModelOutput):
            gateway.complete(request())
        assert harness.provider_calls(gateway) == 2

    def test_one_invalid_answer_then_a_valid_one_succeeds(self, harness: GatewayHarness) -> None:
        gateway = harness.gateway(["{broken", valid_answer()])
        assert gateway.complete(request()).output["confidence"] == "medium"
        assert harness.provider_calls(gateway) == 2

    def test_output_with_extra_properties_is_refused(self, harness: GatewayHarness) -> None:
        extra = valid_answer(approve_credit=True)
        gateway = harness.gateway([extra, extra])
        with pytest.raises(InvalidModelOutput):
            gateway.complete(request())

    def test_an_off_schema_action_is_refused(self, harness: GatewayHarness) -> None:
        bad = valid_answer(action="issue_refund_now")
        gateway = harness.gateway([bad, bad])
        with pytest.raises(InvalidModelOutput):
            gateway.complete(request())

    def test_the_error_never_quotes_the_answer(self, harness: GatewayHarness) -> None:
        """The message is logged; the answer may echo case data (I12, I14)."""
        leaky = f"{SECRET_MARKER} not json"
        gateway = harness.gateway([leaky, leaky])
        with pytest.raises(InvalidModelOutput) as raised:
            gateway.complete(request())
        assert SECRET_MARKER not in str(raised.value)

    def test_usage_is_reported_with_cost_as_a_decimal(self, harness: GatewayHarness) -> None:
        """I9 again: spend caps are compared against these numbers."""
        gateway = harness.gateway(["{bad", valid_answer()])
        usage = gateway.complete(request()).usage
        assert isinstance(usage.cost_usd, Decimal)
        assert usage.cost_usd > 0
        assert usage.input_tokens > 0
        assert usage.output_tokens > 0


class TestErrors:
    def test_a_guardrail_block_raises_guardrail_blocked(self, harness: GatewayHarness) -> None:
        """I15: a block sends the case to a human, it does not retry into success."""
        gateway = harness.gateway(["guardrail", valid_answer()])
        with pytest.raises(GuardrailBlocked):
            gateway.complete(request())
        assert harness.provider_calls(gateway) == 1

    def test_a_rate_limit_raises_transient_with_retry_after(self, harness: GatewayHarness) -> None:
        gateway = harness.gateway(["rate_limit"])
        with pytest.raises(TransientPortError) as raised:
            gateway.complete(request())
        assert raised.value.retry_after_s is not None

    def test_an_auth_failure_raises_permanent_not_transient(self, harness: GatewayHarness) -> None:
        """Retrying a bad credential is how a test account gets locked."""
        gateway = harness.gateway(["auth", valid_answer()])
        with pytest.raises(AuthPortError) as raised:
            gateway.complete(request())
        assert isinstance(raised.value, PermanentPortError)
        assert harness.provider_calls(gateway) == 1


SAMPLING = ("temperature", "top_p", "top_k", "seed", "presence_penalty", "frequency_penalty")


class TestSafety:
    def test_no_sampling_parameters_are_sent(self, harness: GatewayHarness) -> None:
        """foundry.md section 6: the adapters never send them, so runs stay comparable."""
        gateway = harness.gateway([valid_answer()])
        gateway.complete(request())
        sent = arrange(harness.sent_options, gateway)
        assert sent
        for options in sent:
            assert not set(SAMPLING) & set(options), options

    def test_the_haiku_route_refuses_a_non_haiku_model(self, harness: GatewayHarness) -> None:
        """I14: restricted by code, not by whichever config file is loaded."""
        with pytest.raises(Exception, match=r"(?i)haiku"):
            arrange(harness.gateway_for_model, "claude-opus-5-5")

    def test_prompt_and_response_content_never_reaches_telemetry(
        self, harness: GatewayHarness, caplog: pytest.LogCaptureFixture
    ) -> None:
        """I14, unless telemetry.capture_content and APP_ENV=test."""
        answer = valid_answer(rationale=f"{SECRET_MARKER} reasoning")
        gateway = harness.gateway([answer])
        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as captured:
            gateway.complete(request())
        assert SECRET_MARKER not in caplog.text
        assert SECRET_MARKER not in json.dumps(captured, default=str)
