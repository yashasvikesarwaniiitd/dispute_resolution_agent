"""Investigation tools: what a tool is told, and what it answers (ADR 0071).

A tool looks something up for one case. It is **read-only**: it holds no write port, no store
it writes to, no channel (I1). It sees only the case it is called for (`ToolContext`), so a
tool can never be pointed at another seller's case (ADR 0058). Every figure it returns was
computed in code and is written as a string (I9); anything a person or a model wrote travels
as `UntrustedText` (I5).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.evidence import EvidenceResult
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.values import UntrustedText

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ToolSpec(BaseModel):
    """How a model is told about a tool: its name, what it is for, and its arguments."""

    model_config = _FROZEN

    name: str = Field(pattern=r"^[a-z][a-z_]{2,40}$")
    description: str = Field(min_length=1, max_length=600)
    #: JSON schema of the arguments (from the tool's own pydantic model).
    input_schema: dict[str, Any]


class ToolContext(BaseModel):
    """The one case a tool is called for, and the evidence code already gathered for it."""

    model_config = _FROZEN

    case_id: CaseId
    facts: CaseFacts
    evidence: tuple[EvidenceResult, ...] = ()


class ToolOutput(BaseModel):
    """A tool's answer. A failure is an answer too (`ok=False`), never an exception (I4)."""

    model_config = _FROZEN

    tool: str
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    untrusted: tuple[UntrustedText, ...] = ()
    #: Set when `ok` is False: a short code ("no_photo", "bad_arguments", ...).
    error: str | None = None


__all__ = ["ToolContext", "ToolOutput", "ToolSpec"]
