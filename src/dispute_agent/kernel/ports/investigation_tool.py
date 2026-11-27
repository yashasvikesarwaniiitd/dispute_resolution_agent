"""The InvestigationTool port (ADR 0071).

One read-only look-up an agent may choose to make while investigating a case. Each tool lives
in the module that owns its data and is assembled into a `Toolbox` by the composition root,
so modules stay independent and the kernel never imports a tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from dispute_agent.kernel.domain.tools import ToolContext, ToolOutput, ToolSpec


class InvestigationTool(Protocol):
    @property
    def spec(self) -> ToolSpec:
        """Its name, purpose and argument schema."""
        ...

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        """Look it up for this case. Raises pydantic's ValidationError on bad arguments;
        any other failure is returned as `ok=False`, or raised for the Toolbox to record."""
        ...
