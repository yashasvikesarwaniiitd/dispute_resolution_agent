"""A model gateway with canned answers, for tests (I11, I14).

Keyed by prompt name, so a test says "when the case investigator is asked, answer this" and
gets a deterministic run with no network and no spend. It still goes through ModelGateway
rather than around it, which is the point of I14: the fake is wired in at the composition
root exactly where a real route would be, so the code under test takes the same path.

A prompt with no canned answer raises rather than inventing one. A test that reaches an
unexpected model call has found something, and returning a plausible default would hide it.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse, ModelUsage

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.json_values import JsonValue

MODULE = "model_gateway"
ADAPTER = "fake"

ZERO_USAGE = ModelUsage(input_tokens=0, output_tokens=0, latency_ms=0, cost_usd=Decimal("0.00"))


class FakeModelGateway:
    """Implements ModelGateway. Tests only; never wired into a real run."""

    def __init__(
        self,
        responses: Mapping[str, Mapping[str, JsonValue]],
        model_id: str = "fake-model",
    ) -> None:
        self._responses = dict(responses)
        self._model_id = model_id
        self.calls: list[ModelRequest] = []

    def complete(self, req: ModelRequest) -> ModelResponse:
        self.calls.append(req)
        canned = self._responses.get(req.prompt.name)
        if canned is None:
            raise InvalidModelOutput(
                f"no canned response for prompt {req.prompt.name!r}; "
                f"known: {sorted(self._responses)}"
            )
        return ModelResponse(
            output=canned,
            model_id=self._model_id,
            route=ADAPTER,
            usage=ZERO_USAGE,
        )
