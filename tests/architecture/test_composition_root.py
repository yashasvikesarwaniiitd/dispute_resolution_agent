"""Card P0-08: nothing imports the composition root.

`app.py` is allowed to import everything, which is exactly why nothing may import it. A
module that reached into the composition root could pull out any adapter in the system, and
the dependency rule would be a comment rather than a constraint.

import-linter already forbids `kernel -> app`. This covers the rest of `src/`, including the
modules and packs that contract does not name, and the AST walk means a lazy import inside a
function is caught too — that being the usual way this rule gets broken.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "dispute_agent"
APP = SRC / "app.py"

SOURCES = sorted(p for p in SRC.rglob("*.py") if p != APP)


def imported_names(path: Path) -> set[str]:
    """Every module named by an import anywhere in the file, nested ones included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(SRC)))
def test_nothing_imports_app(path: Path) -> None:
    offenders = {
        name for name in imported_names(path) if name.split(".")[:2] == ["dispute_agent", "app"]
    }
    assert not offenders, (
        f"{path.relative_to(SRC)} imports the composition root: {sorted(offenders)}"
    )


def test_the_composition_root_exists_and_is_the_only_one() -> None:
    """One root. A second one would mean two places deciding how the system is assembled."""
    assert APP.is_file()
    roots = [p for p in SRC.rglob("*.py") if "create_app" in p.read_text(encoding="utf-8")]
    assert roots == [APP]


def test_only_the_composition_root_reads_the_environment() -> None:
    """CLAUDE.md section 6: no hidden environment reads.

    Settings arrive through constructors. The one place allowed to look at os.environ is the
    root, because that is where the outside world is turned into arguments. `infra/settings`
    joins it in P0-09, by way of pydantic-settings rather than a bare os.environ read.
    """
    offenders: list[str] = []
    for path in SOURCES:
        source = path.read_text(encoding="utf-8")
        if "os.environ" in source or "os.getenv" in source:
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"these read the environment directly: {offenders}"
