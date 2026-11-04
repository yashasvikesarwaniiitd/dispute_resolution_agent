"""P7-11 proof 1: Phase 7 changed no line in `kernel/` (D3).

`git diff phase-6-complete -- src/dispute_agent/kernel` must be empty - committed changes and
the working tree alike. The tag marks the end of Phase 6, including the one disclosed kernel
change that connected HOOK A (ADR 0035); everything after it is measured here.

If this fails, do not edit the kernel back into shape: the socket was designed wrong, and that
is an ADR and an owner conversation (task card P7-11).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TAG = "phase-6-complete"


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=60
    )


def test_the_kernel_is_unchanged_since_the_phase_6_tag() -> None:
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout: the kernel diff cannot be measured here")
    if _git("rev-parse", "--verify", f"{TAG}^{{commit}}").returncode != 0:
        pytest.fail(f"tag {TAG} is missing: the Phase 7 kernel diff has no baseline")
    diff = _git("diff", "--stat", TAG, "--", "src/dispute_agent/kernel")
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == "", f"kernel changed since {TAG}:\n{diff.stdout}"


def test_no_new_untracked_file_in_the_kernel() -> None:
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    untracked = _git("ls-files", "--others", "--exclude-standard", "src/dispute_agent/kernel")
    assert untracked.stdout.strip() == "", untracked.stdout
