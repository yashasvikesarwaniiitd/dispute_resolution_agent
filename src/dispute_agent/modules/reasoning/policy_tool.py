"""The `lookup_policy` investigation tool (ADR 0071).

Passages from the project's policy rules and escalation guide, by keyword
(`explainer/policy_index.py`, ADR 0040). Decision records are left out: they are engineering
notes, not policy (ADR 0072). Local and free: no network, no model call.
Project documents only - never case data and nothing a seller wrote - so the passages are
trusted text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.tools import ToolOutput, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.kernel.domain.tools import ToolContext
    from dispute_agent.modules.reasoning.explainer.policy_index import Passage


class PolicyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=200, description="Rule id, reason code or words.")
    top_k: int = Field(default=3, ge=1, le=5)


def _default_search(query: str, top_k: int) -> list[Passage]:
    from dispute_agent.modules.reasoning.explainer.policy_index import policy_index

    return policy_index().search(query, top_k)  # built on first use, once per process


class PolicyLookupTool:
    """Implements InvestigationTool."""

    spec = ToolSpec(
        name="lookup_policy",
        description=(
            "Passages from the dispute policy rules (R01-R25) matching a rule id, reason code or "
            "words, or not_found. Use it to check what a rule says before relying on it. It explains "
            "the rules; it never changes how they are applied."
        ),
        input_schema=PolicyArgs.model_json_schema(),
    )

    def __init__(self, search: Callable[[str, int], list[Passage]] = _default_search) -> None:
        self._search = search

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        asked = PolicyArgs.model_validate(dict(args))
        # A zero score shares no word with the query: unrelated text, not an answer (AUD-04).
        ids = re.findall(r"\bR\d{2}\b", asked.query.upper())
        found = self._search(asked.query, asked.top_k)
        if ids:
            # A rule asked for by id comes first: the passage that defines it, found by the id
            # alone (other words in the query can outrank it), not a passage that only cites it.
            found = list({p.text: p for p in self._search(" ".join(ids), 8) + found}.values())
            found.sort(key=lambda p: not any(f"### {i} " in p.text for i in ids))
        passages = [p for p in found if p.score > 0][: asked.top_k]
        if not passages:
            return ToolOutput(
                tool=self.spec.name, ok=True, data={"not_found": True, "passages": []}
            )
        return ToolOutput(
            tool=self.spec.name,
            ok=True,
            data={"passages": [{"source": p.source, "text": p.text} for p in passages]},
        )


__all__ = ["PolicyLookupTool"]
