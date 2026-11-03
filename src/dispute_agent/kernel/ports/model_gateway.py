"""The ModelGateway port (kernel-interfaces.md section 6.4, I14).

The only path to any model. No other port accepts a prompt or returns model output, so every
model call passes one place where budget, guardrails, drift and routing are applied. An
architecture test in P0-10 holds that line as the ports grow.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.reasoning import ModelRequest, ModelResponse


class ModelGateway(Protocol):
    def complete(self, req: ModelRequest) -> ModelResponse:
        """Validates the output against the prompt's schema, with one retry.

        Raises ModuleUnavailable, TransientPortError, InvalidModelOutput, BudgetExceeded,
        GuardrailBlocked, AgentDrift.
        """
        ...
