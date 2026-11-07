"""The model-call log, in its own database file (P4b-02, ADR 0017, ADR 0030).

Why its own file: a model call happens while the case database holds its write lock
(`BEGIN IMMEDIATE`, ADR 0008). A second connection writing the same file would wait on that
lock, and writing through the case's own connection would roll the record back with the
case if anything later failed - losing a call that was really billed. A separate file is
written and committed at once, whatever the case does next, which is what a spending
record has to be.

The table is `model_calls` exactly as schema.sql declares it, minus the one foreign key to
`cases` - a reference across database files cannot be enforced by SQLite.

Cost is stored in whole micro-dollars, rounded **up**: rounding down a million small calls
would let the recorded spend drift below the real bill, and the cap reads this number.
"""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_CEILING, Decimal
from typing import TYPE_CHECKING

from sqlalchemy import func, insert, select

from dispute_agent.infra.sqlite.engine import (
    create_sqlite_engine,
    iter_statements,
    schema_sql_path,
)
from dispute_agent.infra.sqlite.tables import model_calls

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import Engine

    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.domain.model_calls import ModelCallRecord

MICROS = Decimal(1_000_000)


def model_call_ddl() -> list[str]:
    """schema.sql's own statements for this table, so there is no second copy to drift."""
    ddl = schema_sql_path().read_text(encoding="utf-8")
    picked = [s for s in iter_statements(ddl) if "model_calls" in s]
    return [s.replace("REFERENCES cases(id)", "") for s in picked]


def open_model_call_log(path: Path | str) -> SqliteModelUsageLedger:
    engine = create_sqlite_engine(path)
    raw = engine.raw_connection()
    try:
        exists = raw.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_calls'"
        ).fetchone()
        if exists is None:
            for statement in model_call_ddl():
                raw.execute(statement)
            raw.commit()
    finally:
        raw.close()
    return SqliteModelUsageLedger(engine)


class SqliteModelUsageLedger:
    """Implements ModelUsageLedger."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def record(self, call: ModelCallRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(model_calls).values(
                    id=call.id,
                    case_id=None if call.case_id is None else str(call.case_id),
                    purpose=call.purpose,
                    route=call.route,
                    provider=call.route.split("_")[0],
                    deployment=None,
                    model_id=call.model_id,
                    prompt_name=call.prompt_name,
                    prompt_version=call.prompt_version,
                    prompt_hash=call.prompt_hash,
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    latency_ms=call.latency_ms,
                    cost_usd_micros=int(
                        (call.cost_usd * MICROS).to_integral_value(rounding=ROUND_CEILING)
                    ),
                    status=call.status,
                    error_code=call.error_code,
                    failover_from=call.failover_from,
                    created_at=call.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                )
            )

    def calls_for_case(self, case_id: CaseId) -> int:
        with self._engine.connect() as conn:
            count = conn.execute(
                select(func.count())
                .select_from(model_calls)
                .where(model_calls.c.case_id == str(case_id))
                .where(model_calls.c.status != "budget")
            ).scalar_one()
        return int(count)

    def spend_usd(self, route: str) -> Decimal:
        with self._engine.connect() as conn:
            micros = conn.execute(
                select(func.coalesce(func.sum(model_calls.c.cost_usd_micros), 0)).where(
                    model_calls.c.route == route
                )
            ).scalar_one()
        return Decimal(int(micros)) / MICROS

    def usage_for_case(self, case_id: CaseId) -> tuple[int, Decimal]:
        """A case's model calls and what they cost. Read-only; the console (ADR 0044)."""
        with self._engine.connect() as conn:
            calls, micros = conn.execute(
                select(func.count(), func.coalesce(func.sum(model_calls.c.cost_usd_micros), 0))
                .select_from(model_calls)
                .where(model_calls.c.case_id == str(case_id))
            ).one()
        return int(calls), Decimal(int(micros)) / MICROS


__all__ = ["SqliteModelUsageLedger", "model_call_ddl", "open_model_call_log"]
