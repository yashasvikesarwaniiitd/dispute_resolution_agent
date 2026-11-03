"""One row of the model-call log (schema.sql `model_calls`, P4b-02, I14).

What a call cost and how it ended - never what was said. There is deliberately no field
that could hold prompt or answer text, so "content stays out of the log" is a property of
the type rather than a rule a writer has to remember.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.values import require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)

CallStatus = Literal["ok", "invalid_output", "error", "blocked", "budget"]


class ModelCallRecord(BaseModel):
    model_config = _FROZEN

    id: str
    case_id: CaseId | None
    purpose: str
    route: str
    model_id: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    input_tokens: Annotated[int, Field(ge=0)]
    output_tokens: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[int, Field(ge=0)]
    cost_usd: Annotated[Decimal, Field(ge=0)]
    status: CallStatus
    error_code: str | None = None
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return require_aware(value, "created_at")


__all__ = ["CallStatus", "ModelCallRecord"]
