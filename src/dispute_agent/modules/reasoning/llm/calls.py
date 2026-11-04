"""Ask the model, check the answer against what only the caller knows, retry once.

The gateway has already enforced the JSON schema. What it cannot know is the context: that
the items in a reading are the items we asked for, or that every number in a draft cites a
fact we hold. Each LLM adapter supplies that check; this runs it with the same rules for all
three (agent-design.md sections 3 and 4):

- a failed check gets one identical retry, then `InvalidModelOutput`;
- once an answer has been rejected, a retry that cannot be made is *also* invalid output -
  never "unavailable", which would hand a case the model already got wrong to a fallback
  (Phase 4P safety review, M3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    InvalidModelOutput,
    ModuleUnavailable,
    TransientPortError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse
    from dispute_agent.kernel.ports import ModelGateway

ATTEMPTS = 2


def ask_checked(
    gateway: ModelGateway,
    request: ModelRequest,
    check: Callable[[Mapping[str, JsonValue]], None],
) -> ModelResponse:
    last: InvalidModelOutput | None = None
    for _ in range(ATTEMPTS):
        try:
            response = gateway.complete(request)
        except (TransientPortError, ModuleUnavailable) as failed:
            if last is None:
                raise
            raise InvalidModelOutput(
                f"answer rejected, then the retry failed ({failed.code})"
            ) from failed
        try:
            check(response.output)
        except InvalidModelOutput as invalid:
            last = invalid
            continue
        return response
    assert last is not None
    raise InvalidModelOutput(f"checks failed twice; last: {last.args[0]}")


__all__ = ["ATTEMPTS", "ask_checked"]
