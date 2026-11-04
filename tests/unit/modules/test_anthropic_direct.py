"""The Haiku-only secondary route (P4-10, I14): the only provider API key in the project."""

from __future__ import annotations

import json
import logging

import pytest
import structlog
from pydantic import SecretStr

from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    ConfigError,
    InvalidModelOutput,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.modules.model_gateway.anthropic_direct.gateway import (
    AnthropicDirectGateway,
    check_model,
)
from tests.anthropic_stub import MODEL, PRICE, haiku
from tests.model_fixtures import SECRET_MARKER, request, valid_answer

SENTINEL_KEY = "sk-" + "ant-sentinel-0123456789abcdefghijkl"


class TestHaikuOnly:
    @pytest.mark.parametrize("model", ["claude-haiku-5-5", "claude-haiku-4-5"])
    def test_haiku_models_are_accepted(self, model: str) -> None:
        assert check_model(model) == model

    @pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-sonnet-5-5", "", None, "gpt-5"])
    def test_anything_else_is_refused_naming_the_setting(self, model: str | None) -> None:
        with pytest.raises(ConfigError, match="ANTHROPIC_MODEL_HAIKU"):
            check_model(model)

    def test_the_restriction_holds_even_when_built_directly(self) -> None:
        """In the adapter, not in whichever config file happens to be loaded."""
        with pytest.raises(ConfigError):
            AnthropicDirectGateway(model="claude-opus-5-5", price=PRICE, client=object())

    def test_no_key_refuses_to_start(self) -> None:
        with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY"):
            AnthropicDirectGateway.build(model=MODEL, api_key=None, price=PRICE, timeout_s=5)


class TestTheCall:
    def test_a_valid_answer_is_parsed_and_costed(self) -> None:
        gateway, _ = haiku([valid_answer()])
        response = gateway.complete(request())
        assert response.route == "anthropic_direct"
        assert response.usage.cost_usd == PRICE.cost(1000, 200)

    def test_the_system_prompt_goes_in_system_and_nothing_else_is_sent(self) -> None:
        gateway, messages = haiku([valid_answer()])
        gateway.complete(request())
        sent = messages.calls[0]
        assert set(sent) == {"model", "max_tokens", "system", "messages"}
        assert sent["system"] == request().prompt.messages[0].content

    @pytest.mark.parametrize("stop", ["refusal", "max_tokens"])
    def test_a_refusal_or_cut_off_answer_is_invalid(self, stop: str) -> None:
        gateway, _ = haiku([(stop, valid_answer()), (stop, valid_answer())])
        with pytest.raises(InvalidModelOutput, match="cut short"):
            gateway.complete(request())


class TestErrors:
    @pytest.mark.parametrize(
        ("fault", "raised"),
        [
            ("rate_limit", TransientPortError),
            ("server", TransientPortError),
            ("timeout", TransientPortError),
            ("auth", AuthPortError),
            ("bad_request", PermanentPortError),
        ],
    )
    def test_each_failure_maps_to_its_kernel_error(
        self, fault: str, raised: type[Exception]
    ) -> None:
        gateway, _ = haiku([fault])
        with pytest.raises(raised):
            gateway.complete(request())

    def test_a_timeout_is_charged_at_worst_case(self) -> None:
        gateway, _ = haiku(["timeout"])
        with pytest.raises(TransientPortError) as raised:
            gateway.complete(request())
        assert raised.value.usage is not None


class TestNothingLeaks:
    def test_the_key_never_appears_in_logs_or_errors(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        gateway = AnthropicDirectGateway.build(
            model=MODEL, api_key=SecretStr(SENTINEL_KEY), price=PRICE, timeout_s=5
        )
        gateway.client = haiku(["auth"])[0].client
        with (
            caplog.at_level(logging.DEBUG),
            structlog.testing.capture_logs() as captured,
            pytest.raises(AuthPortError) as raised,
        ):
            gateway.complete(request())
        assert SENTINEL_KEY not in str(raised.value)
        assert SENTINEL_KEY not in caplog.text
        assert SENTINEL_KEY not in json.dumps(captured, default=str)
        assert SENTINEL_KEY not in repr(gateway)

    def test_no_content_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        gateway, _ = haiku([valid_answer(rationale=SECRET_MARKER)])
        with caplog.at_level(logging.DEBUG), structlog.testing.capture_logs() as captured:
            gateway.complete(request())
        assert SECRET_MARKER not in caplog.text
        assert SECRET_MARKER not in json.dumps(captured, default=str)
