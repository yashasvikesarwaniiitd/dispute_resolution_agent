"""The single implementation behind every `make` target (CLAUDE.md section 15).

GNU Make is not available on every machine this project is built on, so the Makefile
delegates each target here rather than holding the commands itself. That keeps one
implementation and lets `make verify` mean the same thing on Windows, Linux and CI.

Run directly:   uv run python scripts/make.py verify
Through make:   make verify          (Makefile, make.cmd or ./make.sh)

A target with nothing to run yet exits 0 and names the phase that fills it, so
`make verify` is green from the first commit (card P0-02).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

ROOT = __file__.rsplit("scripts", 1)[0].rstrip("\\/")


def run(*argv: str) -> int:
    """Run one command, streaming its output. Returns its exit code."""
    print(f"$ {' '.join(argv)}", flush=True)
    return subprocess.call(argv, cwd=ROOT)


def pending(target: str, phase: str) -> int:
    print(f"{target}: nothing to run yet - filled in {phase}.")
    return 0


def uv(*argv: str) -> int:
    return run("uv", "run", *argv)


# --- targets ----------------------------------------------------------------


def setup() -> int:
    code = run("uv", "sync", "--python", "3.12")
    if code:
        return code
    return uv("pre-commit", "install")


def lint() -> int:
    code = uv("ruff", "check", "src", "tests", "scripts")
    return code or uv("ruff", "format", "--check", "src", "tests", "scripts")


def typecheck() -> int:
    return uv("mypy")


def arch() -> int:
    return uv("lint-imports")


def test() -> int:
    """Unit, contract, architecture and scenario tests. Never integration (CLAUDE.md section 8)."""
    return uv("pytest", "-m", "not integration and not live_model")


def test_integration() -> int:
    return uv("pytest", "-m", "integration")


def verify() -> int:
    """The definition of done (CLAUDE.md section 14). Fails on the first failing step."""
    for name, step in (("lint", lint), ("typecheck", typecheck), ("arch", arch), ("test", test)):
        print(f"\n=== verify: {name} ===", flush=True)
        code = step()
        if code:
            print(f"\nverify FAILED at {name}", file=sys.stderr)
            return code
    print("\nverify: PASS")
    return 0


def scenarios() -> int:
    """Check the scenario catalogue against the state machine (Stage A5 tooling)."""
    return uv("python", "scripts/check_scenarios.py", ".")


def run_api() -> int:
    return uv("uvicorn", "dispute_agent.app:create_app", "--factory", "--reload")


def run_approvals() -> int:
    """The review queue. A separate process from the API: it is a different audience and a
    different secret, and running it only when someone is reviewing is the point."""
    return uv(
        "streamlit",
        "run",
        "src/dispute_agent/modules/approvals/streamlit_ui/app.py",
    )


def email_login() -> int:
    """The once-per-90-days interactive sign-in (ADR 0023, docs/SETUP_CHANNELS.md).

    A separate target rather than something a send does on demand: the device code flow
    needs a person at a browser, and an unattended run that paused waiting for one would
    look like a hang.
    """
    return uv("python", "-m", "dispute_agent.modules.email.outlook_graph.login")


def demo() -> int:
    """One synthetic dispute against the real test accounts. Refuses without credentials
    rather than quietly demonstrating the fakes."""
    return uv("python", "scripts/demo.py")


def agents_sync() -> int:
    """Create or update the Foundry agent from the prompt (owner only; docs/SETUP_FOUNDRY.md)."""
    return uv("python", "scripts/agents_sync.py")


def foundry_login(*args: str) -> int:
    """Sign in to Azure once, in the browser (no Azure CLI needed). `--device-code` if the
    browser redirect is blocked."""
    return uv("python", "scripts/foundry_login.py", *args)


def foundry_spend() -> int:
    """Estimated spend per route against its cap, and the credit window."""
    return uv("python", "scripts/foundry_spend.py")


def llm_check() -> int:
    """One tiny Haiku call; prints ids and counts only. Spends a fraction of a cent."""
    return uv("python", "scripts/llm_check.py")


def foundry_check() -> int:
    """Is everything in place to call the agent? No model call, no cost."""
    return uv("python", "scripts/foundry_check.py")


def try_agent(*args: str) -> int:
    """One synthetic dispute through the line with the agent deciding. Sends nothing.

    Takes its own arguments (a case name, `--offline`), so it is dispatched before the
    one-target-per-word loop below.
    """
    return uv("python", "scripts/try_agent.py", *args)


def evaluate(*args: str) -> int:
    """Baseline versus model on the scenario catalogue. `--live` uses Foundry (costs)."""
    return uv("python", "scripts/eval.py", *args)


TARGETS = {
    "setup": setup,
    "verify": verify,
    "agents-sync": agents_sync,
    "foundry-check": foundry_check,
    "foundry-spend": foundry_spend,
    "llm-check": llm_check,
    "foundry-login": foundry_login,
    "try-agent": try_agent,
    "test": test,
    "test-integration": test_integration,
    "lint": lint,
    "typecheck": typecheck,
    "arch": arch,
    "scenarios": scenarios,
    "run-api": run_api,
    "email-login": email_login,
    "run-approvals": run_approvals,
    "demo": demo,
    "eval": evaluate,
}


def main(argv: Sequence[str]) -> int:
    if not argv:
        print("targets: " + "  ".join(sorted(TARGETS)))
        return 0
    if argv[0] == "try-agent":
        return try_agent(*argv[1:])
    if argv[0] == "eval":
        return evaluate(*argv[1:])
    if argv[0] == "foundry-login":
        return foundry_login(*argv[1:])
    code = 0
    for name in argv:
        target = TARGETS.get(name)
        if target is None:
            print(f"unknown target: {name}", file=sys.stderr)
            print("targets: " + "  ".join(sorted(TARGETS)), file=sys.stderr)
            return 2
        code = target()
        if code:
            return code
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
