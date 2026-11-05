"""The Case Explainer, on the Claude Agent SDK (ADR 0039).

A reviewer asks a question about a case ("why did this go to a person?"); Claude Haiku answers
by choosing among four read-only tools. It is an assistant to a person, outside the decision
path: it cannot approve, send, credit or change anything, and nothing it says is acted on.

**Read-only by construction, in four layers** (code.claude.com/docs/en/agent-sdk):
1. `tools=[]` - none of Claude Code's built-in tools (no shell, no files, no web) is offered;
2. the only tools that exist are ours, and each one only reads (`CaseReader`, opened by SQLite
   in `mode=ro`, and the local policy index);
3. `permission_mode="dontAsk"` denies anything not pre-approved, and a `PreToolUse` hook denies
   any tool not on our allow-list, as a backstop;
4. `setting_sources=[]` and an empty working folder: no project or user settings are loaded.

**I14, the one exception (ADR 0039).** I14 sends every model call through `ModelGateway`. The
Agent SDK runs its own loop and talks to Anthropic directly, so this is the single, approved
exception, bounded so it cannot touch a case or overspend:
- Haiku only, checked with the same rule as the Haiku failover route (`claude-haiku-*`);
- the key comes from the settings loader and is passed to the SDK process alone;
- a per-question budget (`max_budget_usd`) and a lifetime cap, checked before every question
  against its own usage log (`data/explainer_usage.jsonl`: time, cost, turns - no content);
- no tool output, question or answer is logged.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.modules.reasoning.explainer.reader import CaseNotFoundError, CaseReader

if TYPE_CHECKING:
    from pathlib import Path

    from dispute_agent.modules.reasoning.explainer.policy_index import PolicyIndex

SERVER = "case"
TOOL_NAMES = ("case_overview", "case_timeline", "case_decisions", "policy_lookup")
ALLOWED = tuple(f"mcp__{SERVER}__{name}" for name in TOOL_NAMES)
MAX_TURNS = 8


class ExplainerBudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class Answer:
    text: str
    cost_usd: float
    turns: int
    tools_used: tuple[str, ...]


@dataclass
class UsageLog:
    """Cost only. Never the question, the answer or anything a tool returned (I12, I14)."""

    path: Path

    def spent(self) -> Decimal:
        if not self.path.is_file():
            return Decimal("0")
        total = Decimal("0")
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                total += Decimal(str(json.loads(line).get("cost_usd", 0)))
        return total

    def add(self, cost_usd: float, turns: int, model: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "cost_usd": cost_usd,
            "turns": turns,
            "model": model,
        }
        with self.path.open("a", encoding="utf-8") as out:
            out.write(json.dumps(entry) + "\n")


@dataclass
class ExplainerTools:
    """The four read-only tools, as plain functions (testable without the SDK)."""

    reader: CaseReader
    policies: PolicyIndex

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        try:
            if name == "case_overview":
                return {"case": self.reader.overview(str(args["case_id"]))}
            if name == "case_timeline":
                return {"events": self.reader.timeline(str(args["case_id"]))}
            if name == "case_decisions":
                return self.reader.decisions(str(args["case_id"]))
            if name == "policy_lookup":
                return {
                    "passages": [
                        {"source": p.source, "text": p.text}
                        for p in self.policies.search(str(args["query"]))
                    ]
                }
        except CaseNotFoundError as missing:
            return {"error": f"no case with id {missing.args[0]!r}"}
        except KeyError as absent:
            return {"error": f"missing argument {absent.args[0]!r}"}
        return {"error": f"unknown tool {name!r}"}


@dataclass
class CaseExplainer:
    tools: ExplainerTools
    system_prompt: str
    model: str
    api_key: str
    usage: UsageLog
    per_question_usd: float = 0.05
    lifetime_cap_usd: Decimal = Decimal("0.25")
    _work_dir: str = field(default_factory=lambda: tempfile.mkdtemp(prefix="explainer-"))

    def ask(self, question: str) -> Answer:
        spent = self.usage.spent()
        if spent + Decimal(str(self.per_question_usd)) > self.lifetime_cap_usd:
            raise ExplainerBudgetError(
                f"explainer spend ${spent} would pass its cap ${self.lifetime_cap_usd}"
            )
        answer = asyncio.run(self._ask(question))
        self.usage.add(answer.cost_usd, answer.turns, self.model)
        return answer

    def options(self) -> Any:
        from claude_agent_sdk import ClaudeAgentOptions, HookMatcher

        async def only_ours(
            input_data: dict[str, Any], tool_use_id: Any, context: Any
        ) -> dict[str, Any]:
            if input_data.get("tool_name") in ALLOWED:
                return {}
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "the case explainer may only use its read-only tools",
                }
            }

        return ClaudeAgentOptions(
            tools=[],
            mcp_servers={SERVER: self._server()},
            allowed_tools=list(ALLOWED),
            disallowed_tools=["Agent", "Task"],
            permission_mode="dontAsk",
            setting_sources=[],
            model=self.model,
            max_turns=MAX_TURNS,
            max_budget_usd=self.per_question_usd,
            system_prompt=self.system_prompt,
            cwd=self._work_dir,
            env={"ANTHROPIC_API_KEY": self.api_key},
            hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[cast("Any", only_ours)])]},
        )

    def _server(self) -> Any:
        from claude_agent_sdk import create_sdk_mcp_server, tool

        def make(name: str, description: str, schema: dict[str, Any]) -> Any:
            @tool(name, description, schema)
            async def handler(args: dict[str, Any]) -> dict[str, Any]:
                result = self.tools.call(name, args)
                return {"content": [{"type": "text", "text": json.dumps(result, default=str)}]}

            return handler

        case_id = {"case_id": str}
        return create_sdk_mcp_server(
            name=SERVER,
            version="1.0.0",
            tools=[
                make(
                    "case_overview",
                    "The case's state, review reason, latest assessment and the seller's statement (untrusted).",
                    case_id,
                ),
                make("case_timeline", "The case's last 40 audit events, oldest first.", case_id),
                make(
                    "case_decisions",
                    "Proposals, approvals and actions recorded for the case.",
                    case_id,
                ),
                make(
                    "policy_lookup",
                    "Passages from the project's policy rules and decision records.",
                    {"query": str},
                ),
            ],
        )

    async def _ask(self, question: str) -> Answer:
        from claude_agent_sdk import AssistantMessage, ResultMessage, ToolUseBlock, query

        used: list[str] = []
        text, cost, turns = "", 0.0, 0
        async for message in query(prompt=question, options=self.options()):
            if isinstance(message, AssistantMessage):
                used += [b.name for b in message.content if isinstance(b, ToolUseBlock)]
            elif isinstance(message, ResultMessage):
                text = message.result or ""
                cost = float(message.total_cost_usd or 0.0)
                turns = int(message.num_turns or 0)
        return Answer(text=text.strip(), cost_usd=cost, turns=turns, tools_used=tuple(used))


def check_haiku(model: str | None) -> str:
    """The same rule as the Haiku failover route (I14): `claude-haiku-*` or nothing."""
    import fnmatch

    if not model or not fnmatch.fnmatch(model, "claude-haiku-*"):
        raise ConfigError(f"the case explainer is Haiku only (I14, ADR 0039); {model!r} is refused")
    return model


__all__ = [
    "ALLOWED",
    "Answer",
    "CaseExplainer",
    "ExplainerBudgetError",
    "ExplainerTools",
    "UsageLog",
    "check_haiku",
]
