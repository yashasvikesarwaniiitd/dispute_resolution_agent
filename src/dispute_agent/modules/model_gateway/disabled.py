"""The model gateway, switched off (I6, I14).

With this wired in there is no path to a model at all, which is the point: I14 says every
model call goes through this port, so disabling it disables the AI entirely rather than
leaving some other route open. The investigate step falls back to the rules-only baseline
(orchestrator.md section 5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse

MODULE = "model_gateway"


class DisabledModelGateway:
    """Implements ModelGateway."""

    def complete(self, req: ModelRequest) -> ModelResponse:
        raise ModuleUnavailable(MODULE)
