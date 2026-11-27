"""The Toolbox: the investigation tools one agent may call, by name (ADR 0071).

Built by the composition root from whichever modules are switched on. Calling a tool never
raises: an unknown name, bad arguments or a failing tool all come back as `ok=False` with a
code, so an agent loop records the failure instead of crashing the case (I4). Bad arguments
also say which argument and why, so the agent can fix the call (ADR 0073).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dispute_agent.kernel.domain.tools import ToolOutput

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from dispute_agent.kernel.domain.tools import ToolContext, ToolSpec
    from dispute_agent.kernel.ports.investigation_tool import InvestigationTool


class Toolbox:
    def __init__(self, tools: Iterable[InvestigationTool]) -> None:
        self._tools: dict[str, InvestigationTool] = {}
        for tool in tools:
            name = tool.spec.name
            if name in self._tools:
                raise ValueError(f"two tools are named {name!r}")
            self._tools[name] = tool

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(t.spec for t in self._tools.values())

    def accepted(self, ctx: ToolContext) -> dict[str, str]:
        """Per tool, the argument values this case accepts, in one compact line, for a tool that
        can say (an optional `accepted_values(ctx) -> str`): an agent shown the ids does not
        guess them. A tool whose hint fails is simply shown without one."""
        found: dict[str, str] = {}
        for name, tool in self._tools.items():
            values = getattr(tool, "accepted_values", None)
            if values is None:
                continue
            try:
                text = values(ctx)
            except Exception:  # a hint is never worth a failed case
                continue
            if text:
                found[name] = str(text)
        return found

    def call(
        self, name: str, ctx: ToolContext, args: Mapping[str, Any] | None = None
    ) -> ToolOutput:
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutput(tool=name, ok=False, error="unknown_tool")
        try:
            return tool.run(ctx, args or {})
        except ValidationError as invalid:
            # Which argument and why (never the value given): enough to fix the call.
            problems = [
                f"{'.'.join(map(str, e['loc'])) or 'arguments'}: {e['msg']}"
                for e in invalid.errors()
            ][:5]
            return ToolOutput(
                tool=name, ok=False, error="bad_arguments", data={"problems": problems}
            )
        except Exception as failed:  # a broken tool is a recorded failure, never a crash (I4)
            return ToolOutput(tool=name, ok=False, error=f"tool_failed:{type(failed).__name__}")


__all__ = ["Toolbox"]
