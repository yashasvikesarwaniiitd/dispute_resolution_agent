"""Card P0-02: every command named in CLAUDE.md section 15 exists as a target.

Parses the Makefile and scripts/make.py rather than shelling out to each target:
running them would be slow, and several of them start servers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# CLAUDE.md section 15, verbatim.
REQUIRED = [
    "setup",
    "verify",
    "test",
    "test-integration",
    "lint",
    "typecheck",
    "arch",
    "run-api",
    "run-approvals",
    "demo",
    "eval",
]


def makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text(encoding="utf-8")
    return set(re.findall(r"(?m)^([a-z][a-z0-9-]*):", text))


def runner_targets() -> set[str]:
    text = (ROOT / "scripts" / "make.py").read_text(encoding="utf-8")
    block = text.split("TARGETS = {", 1)[1].split("}", 1)[0]
    return set(re.findall(r'"([a-z][a-z0-9-]*)":', block))


@pytest.mark.parametrize("target", REQUIRED)
def test_makefile_declares_target(target: str) -> None:
    assert target in makefile_targets()


@pytest.mark.parametrize("target", REQUIRED)
def test_runner_implements_target(target: str) -> None:
    assert target in runner_targets()


def test_makefile_and_runner_agree() -> None:
    """A target in one and not the other means `make x` and `make.cmd x` differ."""
    assert makefile_targets() == runner_targets()


def test_shims_exist() -> None:
    """Windows and bash shims, so the target names work without GNU Make."""
    assert (ROOT / "make.cmd").is_file()
    assert (ROOT / "make.sh").is_file()


def test_verify_excludes_integration() -> None:
    """CLAUDE.md section 8: no test touches the network by default."""
    text = (ROOT / "scripts" / "make.py").read_text(encoding="utf-8")
    body = text.split("def test()", 1)[1].split("def ", 1)[0]
    assert "not integration" in body
