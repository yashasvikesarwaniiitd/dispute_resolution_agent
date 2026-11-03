"""A model gateway that answers from a script (P4-04).

Tests and the offline demo run against this, so the whole decide path - prompt, gateway,
checking, proposal, policy - is exercised without a network or a bill (ADR 0029: offline
first, so the 30-day credit window is spent only on what needs a live model).

It is deliberately as unkind as the real thing in the ways the system must survive. An
answer is text, not a dict, so it goes through the same checker as a Foundry answer and can
be malformed; faults raise the same typed errors a provider maps to. A fake that handed back
ready-made dicts would let a reasoner pass tests that a real model would fail on its first
badly-fenced reply.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    GuardrailBlocked,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.modules.model_gateway.output import RawReply, complete_with_one_retry

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse

MODULE = "model_gateway"
ROUTE = "fake"
MODEL_ID = "fake-model"

#: Faults a script step can name instead of an answer.
FAULTS = ("guardrail", "rate_limit", "auth", "unavailable")

#: Notional prices, so the spending cap has real numbers to compare against in tests.
#: Not any provider's tariff.
PRICE_IN_PER_TOKEN = Decimal("0.00000025")
PRICE_OUT_PER_TOKEN = Decimal("0.000002")


@dataclass
class FakeModelGateway:
    """Implements ModelGateway.

    `script` is consumed one step per provider call (a retry consumes a step too). A step is
    the answer text, a dict (serialised as JSON for you), or a fault name from FAULTS. When
    the script runs out, `responder` answers, if one is set; otherwise the call is a fault,
    because an unscripted call in a test is a test that does not know what it is testing.
    """

    script: list[str | dict[str, object]] = field(default_factory=list)
    responder: Callable[[ModelRequest], str | dict[str, object]] | None = None
    requests: list[ModelRequest] = field(default_factory=list)

    def complete(self, req: ModelRequest) -> ModelResponse:
        return complete_with_one_retry(req, self._call, route=ROUTE)

    def provider_calls(self) -> int:
        """How many times the 'provider' was reached, retries included."""
        return len(self.requests)

    def _call(self, req: ModelRequest) -> RawReply:
        self.requests.append(req)
        step = self._next(req)
        if isinstance(step, str) and step in FAULTS:
            _raise(step)
        text = json.dumps(step) if isinstance(step, dict) else step
        prompt_chars = sum(len(m.content) for m in req.prompt.messages)
        # Roughly four characters a token; close enough for a cap to have something to bite.
        tokens_in, tokens_out = prompt_chars // 4 + 1, len(text) // 4 + 1
        return RawReply(
            text=text,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=1,
            cost_usd=tokens_in * PRICE_IN_PER_TOKEN + tokens_out * PRICE_OUT_PER_TOKEN,
            model_id=MODEL_ID,
        )

    def _next(self, req: ModelRequest) -> str | dict[str, object]:
        if self.script:
            return self.script.pop(0)
        if self.responder is not None:
            return self.responder(req)
        raise ModuleUnavailable(MODULE)


def _raise(fault: str) -> None:
    if fault == "guardrail":
        raise GuardrailBlocked("the provider's content filter blocked the input")
    if fault == "rate_limit":
        raise TransientPortError("rate limited", retry_after_s=20.0)
    if fault == "auth":
        raise AuthPortError("the model credentials were refused", status=401)
    raise ModuleUnavailable(MODULE)


__all__ = ["FAULTS", "MODEL_ID", "ROUTE", "FakeModelGateway"]
