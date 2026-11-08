"""Where the console's data lives, and how it reaches the API (ADR 0044).

The same files the running app uses, relative to where it runs. Reads go through a deferred
(non-locking) engine so a screen never waits on the runner's writes. The one write the
console makes - a reviewer's decision - goes through `ReviewConsole` on a normal engine, and
a seller's ticket goes to the API, never into the store directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.sqlite.engine import create_sqlite_engine
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable
    from decimal import Decimal

    from dispute_agent.kernel.domain.external import LedgerEntry
    from dispute_agent.kernel.policy.config import PolicyConfig

DATA = Path("data")
CASES = DATA / "cases.sqlite3"
LEDGER = DATA / "ledger.sqlite3"
MODEL_CALLS = DATA / "model_calls.sqlite3"

#: What a screen says when there is no case store yet: the empty state's one action.
NO_STORE = (
    "There is no case store yet. Create it once with `uv run alembic upgrade head`, "
    "then start the dispute service with `make run-api`."
)


#: The project's config folder - the one `app.py` builds the engine from.
CONFIG = Path(__file__).resolve().parents[3] / "config"


def policy() -> PolicyConfig:
    """The policy the engine runs on (config/policy.yaml): timers, limits, approvers."""
    from dispute_agent.infra.policy import load_policy

    return load_policy(CONFIG)


def approvers() -> list[str]:
    """Who may decide, by name, exactly as the engine checks it (policy.yaml,
    `approvals.approver_ids`; T09, R10, R13). A decision by anyone else is not acted on, so
    Review offers only these names (ADR 0047)."""
    from dispute_agent.infra.policy import load_policy

    ids = load_policy(CONFIG).approvals.approver_ids
    return sorted(i.removeprefix("human:") for i in ids if i.startswith("human:"))


def has_store() -> bool:
    return CASES.is_file()


def reader() -> SqliteUnitOfWork:
    """A read unit of work that never waits on the runner (deferred engine)."""
    return SqliteUnitOfWork(create_sqlite_engine(CASES, deferred=True), clock=SystemClock())


def writer() -> SqliteUnitOfWork:
    """For a reviewer's decision only; everything else the console does is a read."""
    return SqliteUnitOfWork(create_sqlite_engine(CASES), clock=SystemClock())


def ledger_entries(shipment_refs: list[str]) -> list[LedgerEntry]:
    """Every ledger row for these shipments; empty when the ledger has not been created."""
    if not LEDGER.is_file():
        return []
    from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger

    ledger = SimulatedLedger(LEDGER)
    return [entry for ref in shipment_refs for entry in ledger.get_entries(ref)]


def ai_usage(case_id: Any) -> tuple[int, Decimal] | None:
    if not MODEL_CALLS.is_file():
        return None
    from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

    return open_model_call_log(MODEL_CALLS).usage_for_case(case_id)


def poster(
    base_url: str, timeout_s: float
) -> Callable[[dict[str, str]], tuple[int, dict[str, Any]]]:
    """POST /events on the running API. A connection that never happens is a ConnectionError."""

    def post(body: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            answer = httpx.post(f"{base_url.rstrip('/')}/events", json=body, timeout=timeout_s)
        except httpx.TransportError as unreachable:
            raise ConnectionError(str(unreachable)) from unreachable
        try:
            parsed = answer.json()
        except ValueError:
            parsed = {}
        return answer.status_code, parsed if isinstance(parsed, dict) else {}

    return post


__all__ = [
    "CASES",
    "LEDGER",
    "MODEL_CALLS",
    "NO_STORE",
    "ai_usage",
    "has_store",
    "ledger_entries",
    "poster",
    "reader",
    "writer",
]
