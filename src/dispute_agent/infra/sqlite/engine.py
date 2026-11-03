"""The SQLite engine, with the pragmas the schema depends on (schema.sql header).

Four of these are not tuning. `foreign_keys` is **off by default in SQLite**, and without it
every `REFERENCES` clause in the schema is a comment — which is the card's warning worth
repeating: a silently ignored pragma makes several later tests pass for the wrong reason.
`verify_pragmas` exists so that failure is loud at startup rather than quiet forever.

`BEGIN IMMEDIATE` on every write transaction is ADR 0008. SQLite's default deferred
transaction takes the write lock at the first write, so two handlers can both read a case at
version 3, both decide, and only then collide — losing whichever committed second after it
has already called an external service. Taking the lock up front turns that into a wait.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, event, text

from dispute_agent.kernel.domain.errors import ConfigError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine

#: schema.sql uses STRICT tables, which need 3.37.
MINIMUM_SQLITE = (3, 37, 0)

PRAGMAS: dict[str, str] = {
    "foreign_keys": "ON",
    "journal_mode": "WAL",
    "synchronous": "FULL",
    "busy_timeout": "5000",
}


def _apply_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        for name, value in PRAGMAS.items():
            cursor.execute(f"PRAGMA {name} = {value}")
    finally:
        cursor.close()


def _begin_immediate(conn: Any) -> None:
    """ADR 0008: take the write lock up front, not at the first write."""
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def create_sqlite_engine(path: Path | str, *, echo: bool = False) -> Engine:
    """Build an engine with the pragmas applied to every connection.

    In-memory databases get a shared cache so a test can hold more than one connection to the
    same database — without it, each connection would get its own empty one and the
    concurrency tests would pass while testing nothing.
    """
    if sqlite3.sqlite_version_info < MINIMUM_SQLITE:
        raise ConfigError(
            f"SQLite {'.'.join(map(str, MINIMUM_SQLITE))} or newer is required for STRICT "
            f"tables; this interpreter has {sqlite3.sqlite_version}"
        )

    if str(path) == ":memory:":
        url = "sqlite+pysqlite:///:memory:"
        engine = create_engine(url, echo=echo, poolclass=None)
    else:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite+pysqlite:///{path}", echo=echo)

    event.listen(engine, "connect", _apply_pragmas)
    # Replace SQLAlchemy's implicit BEGIN with BEGIN IMMEDIATE.
    event.listen(engine, "begin", _begin_immediate)
    return engine


def verify_pragmas(engine: Engine) -> None:
    """Fail at startup if the pragmas did not take (CLAUDE.md section 6).

    `foreign_keys` is the one that matters: SQLite accepts the pragma silently inside a
    transaction and ignores it, so "we set it" is not evidence that it is on.
    """
    with engine.connect() as conn:
        foreign_keys = conn.execute(text("PRAGMA foreign_keys")).scalar()
        if not foreign_keys:
            raise ConfigError(
                "PRAGMA foreign_keys is off; every REFERENCES clause in the schema would be "
                "decoration and several tests would pass for the wrong reason"
            )
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        if str(journal).lower() not in ("wal", "memory"):
            raise ConfigError(f"expected WAL journal mode, got {journal!r}")


def apply_schema(engine: Engine, schema_sql: str) -> None:
    """Execute the reference DDL verbatim.

    `executescript` on the raw driver connection rather than a statement splitter of our own:
    schema.sql contains triggers whose bodies hold semicolons, and a naive split on ";" would
    silently truncate `tr_actions_status` — the trigger that stops an action moving from
    FAILED back to SENT. Verbatim execution is also the strongest form of "transcribe the
    constraints": there is no transcription to get wrong.
    """
    raw = engine.raw_connection()
    try:
        raw.executescript(schema_sql)
        raw.commit()
    finally:
        raw.close()


def schema_sql_path() -> Path:
    """Where the reference DDL lives. It is the authority (schema.sql header)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "docs" / "spec" / "schema.sql"
        if candidate.is_file():
            return candidate
    raise ConfigError("cannot find docs/spec/schema.sql from " + str(here))


def iter_statements(schema_sql: str) -> Iterator[str]:
    """Split the DDL into statements, respecting trigger bodies.

    Used by the conformance test to count objects, not to create them.
    """
    buffer: list[str] = []
    depth = 0
    for line in schema_sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buffer.append(line)
        upper = stripped.upper()
        if upper.startswith("BEGIN"):
            depth += 1
        if depth and "END;" in upper:
            depth -= 1
            if depth == 0:
                yield "\n".join(buffer)
                buffer = []
            continue
        if depth == 0 and stripped.endswith(";"):
            yield "\n".join(buffer)
            buffer = []
    if buffer:
        yield "\n".join(buffer)
