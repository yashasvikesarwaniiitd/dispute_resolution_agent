"""Nothing in the system imports the operations console (ADR 0044).

The console is a shell like `api/`: it reads stores and posts to the API. If anything in
`kernel`, `modules`, `packs`, `infra`, `api` or `app` came to import `dispute_agent.ui`,
a screen could become part of a decision. The import-linter contracts are fixed verbatim by
ARCHITECTURE.md section 2 (`test_imports.py`), so this rule is a test of its own.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "dispute_agent"


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


@pytest.mark.parametrize(
    "path",
    [p for p in SRC.rglob("*.py") if "ui" not in p.relative_to(SRC).parts[:1]],
    ids=lambda p: str(p.relative_to(SRC)),
)
def test_no_part_of_the_system_imports_the_console(path: Path) -> None:
    assert not any(
        name == "dispute_agent.ui" or name.startswith("dispute_agent.ui.")
        for name in _imports(path)
    )


def test_the_console_posts_tickets_and_never_opens_a_case_itself() -> None:
    """The seller screen goes through `POST /events`; intake stays the API's (I11, dedupe)."""
    for path in (SRC / "ui").rglob("*.py"):
        assert "open_or_get_case" not in path.read_text(encoding="utf-8"), path
