"""Card P1-01: the Core tables match the database the migration actually builds.

The migration executes `schema.sql` verbatim rather than building the schema in SQLAlchemy
Core, which removes the risk of a mis-transcribed constraint but introduces a smaller one:
`tables.py` is used to *query* that schema, and nothing would notice if a column were renamed
in one and not the other until a query failed at runtime.

So this reads the real schema out of SQLite and compares. It is the same drift-test shape as
the enum and transition tests: the file on disk is the authority, read at test time.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from dispute_agent.infra.sqlite.engine import (
    create_sqlite_engine,
    iter_statements,
    schema_sql_path,
    verify_pragmas,
)
from dispute_agent.infra.sqlite.tables import ALL_TABLES, metadata

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = schema_sql_path().read_text(encoding="utf-8")


def columns_of(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


class TestTheCoreTablesMatchTheDdl:
    def test_the_same_tables_exist(self, db: sqlite3.Connection) -> None:
        actual = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith("sqlite_")
        }
        assert set(ALL_TABLES) == actual

    @pytest.mark.parametrize("table", ALL_TABLES)
    def test_the_same_columns_exist(self, db: sqlite3.Connection, table: str) -> None:
        declared = {column.name for column in metadata.tables[table].columns}
        assert declared == columns_of(db, table), f"{table} has drifted from schema.sql"

    @pytest.mark.parametrize("table", ALL_TABLES)
    def test_nullability_agrees(self, db: sqlite3.Connection, table: str) -> None:
        """A column Core thinks is optional and the schema requires is a crash waiting."""
        required = {row[1] for row in db.execute(f"PRAGMA table_info({table})") if row[3]}
        declared = {c.name for c in metadata.tables[table].columns if not c.nullable}
        assert declared == required, f"{table}: nullability differs from schema.sql"


class TestTheEngineAppliesItsPragmas:
    def test_foreign_keys_are_on(self, tmp_path: Path) -> None:
        """The card's warning, as a test: without this several later tests pass for nothing."""
        engine = create_sqlite_engine(tmp_path / "t.sqlite3")
        verify_pragmas(engine)
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1

    def test_write_ahead_logging_is_on(self, tmp_path: Path) -> None:
        engine = create_sqlite_engine(tmp_path / "t.sqlite3")
        with engine.connect() as conn:
            assert str(conn.exec_driver_sql("PRAGMA journal_mode").scalar()).lower() == "wal"

    def test_a_write_transaction_takes_the_lock_immediately(self, tmp_path: Path) -> None:
        """ADR 0008. Two handlers must not both read, decide, and only then collide."""
        engine = create_sqlite_engine(tmp_path / "t.sqlite3")
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE probe (x INTEGER)")
        second = create_sqlite_engine(tmp_path / "t.sqlite3")
        with engine.begin() as conn, pytest.raises(Exception, match=r"locked|busy"):
            conn.exec_driver_sql("INSERT INTO probe VALUES (1)")
            with second.begin() as other:
                other.exec_driver_sql("INSERT INTO probe VALUES (2)")


class TestTheMigration:
    def test_upgrade_head_builds_the_whole_schema(self, tmp_path: Path) -> None:
        """The card's acceptance command, run for real rather than asserted about."""
        database = tmp_path / "fresh.sqlite3"
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=os.environ | {"DISPUTE_AGENT_DB": str(database)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        conn = sqlite3.connect(database)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            if not row[0].startswith(("sqlite_", "alembic_"))
        }
        from dispute_agent.infra.sqlite.patterns import TABLES as PATTERN_TABLES

        # 0002 adds the cross-case tables (Phase 7); schema.sql's own set is unchanged.
        assert tables == set(ALL_TABLES) | set(PATTERN_TABLES)
        assert (
            conn.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger'").fetchone()[0]
            == 9
        )


class TestTheReferenceDdlItself:
    def test_every_statement_parses(self) -> None:
        """A guard on the splitter used for counting, not for creating."""
        statements = list(iter_statements(SCHEMA))
        creates = [s for s in statements if s.strip().upper().startswith("CREATE")]
        assert len(creates) >= 25

    def test_the_trigger_bodies_survive_splitting(self) -> None:
        """A naive split on ";" truncates every trigger, and the truncated version still
        looks like valid SQL — which is why the migration does not use a splitter at all."""
        triggers = [s for s in iter_statements(SCHEMA) if "CREATE TRIGGER" in s.upper()]
        assert len(triggers) == 9
        assert all(s.strip().upper().endswith("END;") for s in triggers)
