"""P4b-12: no credential is in the repository (I12, I14).

Phase 4P is when the project first holds a cloud connection, which is when this check starts
mattering most: an endpoint and a deployment name are fine to commit, a key never is. The
Foundry route uses Entra ID and has no key at all, so any key-shaped string in the repo is
either a mistake or test data pretending to be a secret - both worth failing on.

It scans every file git would commit (tracked, plus untracked files that are not ignored),
so a secret pasted into a new file is caught before its first commit rather than after.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: Shapes of real credentials. Deliberately not "any long random string": a scanner that
#: flags every hash gets an allow-list, and allow-lists are where secrets hide.
SHAPES = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenAI-style key": r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}",
    "Atlassian token": r"ATATT[A-Za-z0-9_=-]{20,}",
    "JSON web token": r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.",
    "Azure storage key": r"AccountKey=[A-Za-z0-9+/=]{20,}",
    "App Insights key": r"InstrumentationKey=[0-9a-fA-F-]{36}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "GitHub token": r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    "Slack token": r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    "Azure OpenAI key header": r"api-key:\s*[0-9a-f]{32}",
}

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".sqlite3", ".bin", ".lock"}


def committable_files() -> list[Path]:
    def git(*args: str) -> list[str]:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout
        return [line for line in out.splitlines() if line]

    names = set(git("ls-files")) | set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(ROOT / name for name in names if Path(name).suffix not in SKIP_SUFFIXES)


FILES = committable_files()


def test_the_scan_sees_the_repository() -> None:
    """Without this, a broken `git ls-files` would make the scan pass by scanning nothing."""
    assert len(FILES) > 100
    assert ROOT / "CLAUDE.md" in FILES


@pytest.mark.parametrize("name", sorted(SHAPES))
def test_no_file_holds_a_credential_of_this_shape(name: str) -> None:
    pattern = re.compile(SHAPES[name])
    hits = []
    for path in FILES:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if pattern.search(text):
            hits.append(str(path.relative_to(ROOT)))
    assert not hits, f"{name} found in: {hits}"


def test_the_env_file_itself_is_ignored() -> None:
    """The one place real values live must never be committable."""
    result = subprocess.run(
        ["git", "check-ignore", "-q", ".env"], cwd=ROOT, capture_output=True, check=False
    )
    assert result.returncode == 0


def test_the_model_call_log_is_ignored() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "-q", "data/model_calls.sqlite3"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
