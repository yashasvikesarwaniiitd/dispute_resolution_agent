"""P6-08: every threat in the threat model maps to tests that exist.

The threat model cites test-charter ids that the tests do not carry, so its coverage claim could
not be checked. docs/SECURITY_REVIEW.md maps each threat to real test node ids; this test reads
both documents and fails if a threat is missing from the map, or a named test does not exist.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
THREATS = sorted(
    set(
        re.findall(
            r"^\| (T\d{2}) \|", (ROOT / "docs/spec/threat-model.md").read_text("utf-8"), re.M
        )
    )
)
REVIEW = (ROOT / "docs/SECURITY_REVIEW.md").read_text(encoding="utf-8")


def mapped() -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for line in REVIEW.splitlines():
        match = re.match(r"^\| (T\d{2}) \| (.+?) \|", line)
        if match:
            rows[match.group(1)] = re.findall(r"`([^`]+::[^`]+)`", match.group(2))
    return rows


MAP = mapped()


def test_the_threat_model_has_threats() -> None:
    assert len(THREATS) >= 22


@pytest.mark.parametrize("threat", THREATS)
def test_every_threat_names_at_least_one_test(threat: str) -> None:
    assert MAP.get(threat), f"{threat} has no test in docs/SECURITY_REVIEW.md"


@pytest.mark.parametrize(
    "node", sorted({node for nodes in MAP.values() for node in nodes}), ids=lambda n: n[-60:]
)
def test_every_named_test_exists(node: str) -> None:
    path, *names = node.split("::")
    source = ROOT / path
    assert source.is_file(), f"no file {path}"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    scope: list[ast.stmt] = list(tree.body)
    for name in names:
        found = next(
            (
                n
                for n in scope
                if isinstance(n, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
                and n.name == name
            ),
            None,
        )
        assert found is not None, f"{node}: no {name}"
        scope = list(getattr(found, "body", []))
