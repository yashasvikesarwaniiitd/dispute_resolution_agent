"""The Haiku-only secondary route (P4-10, I14): the only provider API key in the project."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import ModelRequest

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
        assert set(sent) == {"model", "max_tokens", "system", "messages", "output_config"}
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


class TestThePromptCache:
    """Audit AUD-12/AUD-16: what is marked for the cache, and what a cached call costs."""

    def test_cache_writes_and_reads_are_priced_at_the_provider_s_multiples(self) -> None:
        from decimal import Decimal

        from dispute_agent.modules.model_gateway.anthropic_direct.gateway import cached_cost
        from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

        price = Price(input_per_mtok=Decimal(1), output_per_mtok=Decimal(5), source="test")
        # 1M plain + 1M written at 1.25x + 1M read at 0.1x + 1M output at 5.
        assert cached_cost(price, 10**6, 10**6, 10**6, 10**6) == Decimal("7.35")
        assert cached_cost(price, 1000, 0, 0, 100) == price.cost(1000, 100)

    def test_a_long_system_prompt_is_cached_and_a_short_one_is_sent_as_is(self) -> None:
        from dispute_agent.modules.model_gateway.anthropic_direct.gateway import (
            CACHE_MIN_CHARS,
            _system,
        )

        assert _system("short") == "short"
        long = "x" * CACHE_MIN_CHARS
        assert _system(long) == [
            {"type": "text", "text": long, "cache_control": {"type": "ephemeral"}}
        ]

    def test_the_case_briefing_is_cached_and_the_turns_so_far_are_not(self) -> None:
        from dispute_agent.kernel.domain.reasoning import PROMPT_CACHE_BREAK
        from dispute_agent.modules.model_gateway.anthropic_direct.gateway import (
            CACHE_MIN_CHARS,
            _user,
        )

        briefing = "b" * CACHE_MIN_CHARS
        blocks = _user(f"{briefing}\n{PROMPT_CACHE_BREAK}\nnotes so far")
        assert isinstance(blocks, list) and len(blocks) == 2
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert str(blocks[0]["text"]).endswith(PROMPT_CACHE_BREAK)
        assert blocks[1] == {"type": "text", "text": "\nnotes so far"}
        assert "cache_control" not in blocks[1]
        # Too short to cache, or no marker (every other prompt): one plain message.
        assert _user(f"short\n{PROMPT_CACHE_BREAK}\nnotes") == f"short\n{PROMPT_CACHE_BREAK}\nnotes"
        assert _user(briefing) == briefing


class TestNativeStructuredOutputs:
    """ADR 0073: the prompt's schema, enforced by the API's grammar on this route only; the
    full schema is still checked by parse() on every answer."""

    @staticmethod
    def asking(schema: dict[str, object]) -> ModelRequest:
        req = request()
        return req.model_copy(
            update={"prompt": req.prompt.model_copy(update={"output_schema": schema})}
        )

    def test_the_grammar_is_the_prompt_schema_without_what_it_cannot_enforce(self) -> None:
        gateway, messages = haiku([valid_answer()])
        gateway.complete(request())
        fmt = messages.calls[0]["output_config"]["format"]
        assert fmt["type"] == "json_schema"
        nodes = _nodes(fmt["schema"])
        for dropped in ("maxLength", "maxItems", "minLength", "pattern", "$schema"):
            assert not any(dropped in n and not isinstance(n.get(dropped), dict) for n in nodes), (
                dropped
            )
        objects = [n for n in nodes if n.get("type") == "object"]
        assert objects and all(n["additionalProperties"] is False for n in objects)

    def test_free_form_tool_arguments_go_as_pairs_and_come_back_as_an_object(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["step", "arguments"],
            "properties": {"step": {"type": "string"}, "arguments": {"type": "object"}},
        }
        pairs = [{"key": "packaging_id", "value": "PKG-A"}, {"key": "limit", "value": 5}]
        gateway, messages = haiku([{"step": "search_past_shipments", "arguments": pairs}])
        response = gateway.complete(self.asking(schema))
        assert response.output["arguments"] == {"packaging_id": "PKG-A", "limit": 5}
        sent = messages.calls[0]["output_config"]["format"]["schema"]["properties"]["arguments"]
        assert sent["type"] == "array"

    def test_a_schema_the_api_refuses_is_sent_without_a_format_from_then_on(self) -> None:
        import anthropic
        import httpx2 as httpx

        refused = anthropic.BadRequestError(
            "output_config.format: schema is too complex for compilation",
            response=httpx.Response(400, request=httpx.Request("POST", "https://x.invalid")),
            body=None,
        )
        gateway, messages = haiku([refused, valid_answer(), valid_answer()])
        gateway.complete(request())
        gateway.complete(request())
        assert ["output_config" in c for c in messages.calls] == [True, False, False]

    def test_a_schema_past_the_documented_limits_is_sent_as_before(self) -> None:
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {f"f{n}": {"type": "string"} for n in range(30)},  # 30 optional
        }
        gateway, messages = haiku([{}])
        gateway.complete(self.asking(schema))
        assert "output_config" not in messages.calls[0]

    def test_the_retry_says_what_was_wrong_in_the_same_call_budget(self) -> None:
        wrong = valid_answer()
        wrong["action_parameters"] = {"reason_code": "x", "note": "extra"}
        gateway, messages = haiku([wrong, valid_answer()])
        response = gateway.complete(request())
        assert len(messages.calls) == 2 and response.usage.input_tokens == 2000
        retry = messages.calls[1]["messages"][0]["content"]
        retry = retry if isinstance(retry, str) else retry[-1]["text"]
        assert "previous answer was not accepted" in retry
        assert "field not allowed: note" in retry and "extra" not in retry  # names, never values


def _nodes(node: object) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found += _nodes(value)
    elif isinstance(node, list):
        for value in node:
            found += _nodes(value)
    return found


def test_the_sent_log_records_how_the_format_was_enforced() -> None:
    gateway, _ = haiku([valid_answer()])
    gateway.complete(request())
    assert gateway.sent_log[0]["mode"] == "native" and "latency_ms" in gateway.sent_log[0]
    gateway.structured_outputs = False
    gateway.complete(request())
    assert gateway.sent_log[-1]["mode"] == "plain"
