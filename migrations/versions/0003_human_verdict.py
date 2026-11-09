"""A person can decide a case (ADR 0050): two closed lists gain one value each.

Revision ID: 0003_human_verdict
Revises: 0002_patterns
Create Date: 2026-11-10

- `inbox.event_type` accepts `HUMAN_DECIDED_CASE`.
- `proposals.reasoner` accepts `human`.

schema.sql carries both (0001 runs it verbatim, so a new database already has them). This
brings an existing database to the same definition. Only a CHECK list widens, so no stored row
changes and none can fail the new check. SQLite cannot alter a CHECK in place, and the usual
copy-drop-rename would trip the foreign key `messages.inbox_id -> inbox(id)` while the table is
gone. So the stored table definition is edited in place, the documented route for a change that
leaves the file format alone (sqlite.org/lang_altertable.html, section 7), followed by SQLite's
own integrity check. Idempotent: a database that already has the value is left alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence
    from sqlite3 import Connection

revision: str = "0003_human_verdict"
down_revision: str | None = "0002_patterns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (table, the list as it was, the list as it is now, the value added). From schema.sql.
WIDENED: tuple[tuple[str, str, str, str], ...] = (
    (
        "inbox",
        "'HUMAN_RETURNED_CASE','HUMAN_REOPENED_CASE','HUMAN_CONFIRMED_WITHDRAWAL'))",
        "'HUMAN_RETURNED_CASE','HUMAN_REOPENED_CASE','HUMAN_CONFIRMED_WITHDRAWAL',"
        "'HUMAN_DECIDED_CASE'))",
        "'HUMAN_DECIDED_CASE'",
    ),
    (
        "proposals",
        "CHECK (reasoner IN ('rules_only','llm'))",
        "CHECK (reasoner IN ('rules_only','llm','human'))",
        "'llm','human'",  # the list itself: a comment may mention 'human'
    ),
)


def widen(conn: Connection, changes: tuple[tuple[str, str, str, str], ...] = WIDENED) -> int:
    """Apply each change the database lacks. Returns how many were applied."""
    applied = 0
    for table, before, after, added in changes:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        if row is None:
            raise RuntimeError(f"table {table} is missing; run 0001 first")
        sql = str(row[0])
        if added in sql:  # a database built from today's schema.sql, or already widened
            continue
        if sql.count(before) != 1:
            raise RuntimeError(f"{table}: the list to widen was not found exactly once")
        version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
        conn.execute("PRAGMA writable_schema = ON")
        try:
            conn.execute(
                "UPDATE sqlite_master SET sql = ? WHERE type = 'table' AND name = ?",
                (sql.replace(before, after), table),
            )
            conn.execute(f"PRAGMA schema_version = {version + 1}")
        finally:
            conn.execute("PRAGMA writable_schema = OFF")
        applied += 1
    verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if verdict != "ok":
        raise RuntimeError(f"integrity check after widening: {verdict}")
    return applied


def upgrade() -> None:
    widen(op.get_bind().connection.driver_connection)  # type: ignore[arg-type]


def downgrade() -> None:
    # Narrowing could strand rows that use the new values; a database that has them keeps them.
    raise NotImplementedError("0003 is not reversible: rows may already use the new values")
