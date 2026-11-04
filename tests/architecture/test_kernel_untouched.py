"""P7-11 proof 1, and the rule that replaced it once Phase 7 closed (D3, ADR 0038).

1. **Phase 7 changed no kernel line.** `git diff phase-6-complete phase-7-complete --
   src/dispute_agent/kernel` is empty. Both tags are fixed, so this proof is permanent: no
   later change can make it pass or fail differently.
2. **After Phase 7, every kernel change is a recorded decision.** Each commit since
   `phase-7-complete` that touches `kernel/` must name an ADR in its message (`ADR 0038`).
   A kernel edit with no ADR fails here - the same discipline that made the D3 claim
   checkable, carried forward instead of frozen.

`phase-6-complete` includes the one disclosed change that connected HOOK A (ADR 0035).

If either fails, do not edit the kernel back into shape: write the ADR, or talk to the owner.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PHASE_6 = "phase-6-complete"
PHASE_7 = "phase-7-complete"
KERNEL = "src/dispute_agent/kernel"
ADR = re.compile(r"\bADR[ -]?\d{4}\b")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=60
    )


def _needs(*tags: str) -> None:
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout: the kernel diff cannot be measured here")
    for tag in tags:
        if _git("rev-parse", "--verify", f"{tag}^{{commit}}").returncode != 0:
            pytest.fail(f"tag {tag} is missing: the kernel rule has no baseline")


def test_phase_7_changed_no_kernel_line() -> None:
    _needs(PHASE_6, PHASE_7)
    diff = _git("diff", "--stat", PHASE_6, PHASE_7, "--", KERNEL)
    assert diff.returncode == 0, diff.stderr
    assert diff.stdout.strip() == "", f"kernel changed during Phase 7:\n{diff.stdout}"


def test_every_kernel_change_after_phase_7_names_an_adr() -> None:
    _needs(PHASE_7)
    log = _git("log", "--format=%H%x1f%B%x1e", f"{PHASE_7}..HEAD", "--", KERNEL)
    assert log.returncode == 0, log.stderr
    missing = []
    for entry in log.stdout.split("\x1e"):
        if not entry.strip():
            continue
        sha, _, message = entry.strip().partition("\x1f")
        if not ADR.search(message):
            missing.append(sha[:7])
    assert not missing, f"kernel commits without an ADR in the message: {missing}"


def test_no_new_untracked_file_in_the_kernel() -> None:
    if shutil.which("git") is None or _git("rev-parse", "--git-dir").returncode != 0:
        pytest.skip("not a git checkout")
    untracked = _git("ls-files", "--others", "--exclude-standard", KERNEL)
    assert untracked.stdout.strip() == "", untracked.stdout
