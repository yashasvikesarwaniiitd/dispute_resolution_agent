"""A stand-in for `anthropic.Anthropic().messages`, for the Haiku route's offline tests.

It raises the SDK's own exception types (built on real `httpx2` responses), so the route's
error mapping is exercised exactly as it would be against the real API.
"""

from __future__ import annotations

import json
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2 as httpx

from dispute_agent.modules.model_gateway.anthropic_direct.gateway import AnthropicDirectGateway
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import Price

MODEL = "claude-haiku-5-5"
PRICE = Price(input_per_mtok=Decimal("0.10"), output_per_mtok=Decimal("0.50"), source="test")


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status, headers=headers or {}, request=httpx.Request("POST", "https://x.invalid")
    )


def fault(name: str) -> Exception:
    if name == "rate_limit":
        return anthropic.RateLimitError(
            "slow", response=_response(429, {"retry-after": "2"}), body=None
        )
    if name == "auth":
        return anthropic.AuthenticationError("no", response=_response(401), body=None)
    if name == "timeout":
        return anthropic.APITimeoutError(request=httpx.Request("POST", "https://x.invalid"))
    if name == "server":
        return anthropic.InternalServerError("oops", response=_response(529), body=None)
    if name == "bad_request":
        return anthropic.BadRequestError("bad", response=_response(400), body=None)
    raise ValueError(name)


class StubMessages:
    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []
        self.last: Any = None

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.script:
            step = self.script.pop(0)
            self.last = step
        else:
            step = self.last
        if isinstance(step, str) and step in (
            "rate_limit",
            "auth",
            "timeout",
            "server",
            "bad_request",
        ):
            raise fault(step)
        stop = "end_turn"
        if isinstance(step, tuple):
            stop, step = step
        text = json.dumps(step) if isinstance(step, dict) else str(step)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            stop_reason=stop,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
            model=MODEL,
        )


def haiku(script: list[Any]) -> tuple[AnthropicDirectGateway, StubMessages]:
    messages = StubMessages(script)
    gateway = AnthropicDirectGateway(
        model=MODEL, price=PRICE, client=SimpleNamespace(messages=messages)
    )
    return gateway, messages
