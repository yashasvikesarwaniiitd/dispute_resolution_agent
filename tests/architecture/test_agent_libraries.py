"""The three agent libraries stay where ADRs 0039-0041 put them, and the explainer stays read-only.

- `claude_agent_sdk` is used only by the case explainer, and the explainer is built only in
  app.py - the same rule as every other route to a model (test_guarded_model_path.py).
- The explainer's package imports nothing that can write: no executor, no write port, no
  channel, ticketing or ledger module, no unit of work.
- `langgraph` is used only by the graph investigator, `llama_index` only by the policy index.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "dispute_agent"
EXPLAINER = SRC / "modules" / "reasoning" / "explainer"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def _users(prefix: str) -> set[str]:
    return {
        str(p.relative_to(SRC)).replace("\\", "/")
        for p in SRC.rglob("*.py")
        if any(i == prefix or i.startswith(prefix + ".") for i in _imports(p))
    }


@pytest.mark.parametrize(
    ("library", "allowed"),
    [
        ("claude_agent_sdk", {"modules/reasoning/explainer/agent.py"}),
        ("llama_index", {"modules/reasoning/explainer/policy_index.py"}),
        ("langgraph", {"modules/reasoning/llm/graph.py"}),
    ],
)
def test_each_library_lives_in_one_place(library: str, allowed: set[str]) -> None:
    assert _users(library) <= allowed, f"{library} imported outside {sorted(allowed)}"


WRITERS = (
    "dispute_agent.kernel.executor",
    "dispute_agent.modules.email",
    "dispute_agent.modules.ticketing",
    "dispute_agent.modules.ledger",
    "dispute_agent.infra.sqlite.unit_of_work",
    "dispute_agent.api",
)


@pytest.mark.parametrize(
    "path",
    [*sorted(EXPLAINER.glob("*.py")), SRC / "modules" / "reasoning" / "llm" / "agentic.py"],
    ids=lambda p: p.name,
)
def test_the_explainer_and_the_agent_import_nothing_that_writes(path: Path) -> None:
    bad = {i for i in _imports(path) if i.startswith(WRITERS)}
    assert not bad, f"{path.name} imports a writer: {sorted(bad)}"


def test_the_explainer_is_built_only_in_the_composition_root() -> None:
    builders = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "CaseExplainer":
                    builders.add(str(path.relative_to(SRC)).replace("\\", "/"))
    assert builders <= {"app.py"}, builders
