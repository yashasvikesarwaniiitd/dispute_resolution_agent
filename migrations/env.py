"""Alembic environment.

The database URL comes from `DISPUTE_AGENT_DB` or defaults to `data/cases.sqlite3`, so the
same migration runs against a test file and the real one without editing `alembic.ini`.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

from dispute_agent.infra.sqlite.engine import create_sqlite_engine
from dispute_agent.infra.sqlite.tables import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

DEFAULT_DB = "data/cases.sqlite3"


def database_path() -> str:
    return os.environ.get("DISPUTE_AGENT_DB", DEFAULT_DB)


def run_migrations_offline() -> None:
    context.configure(
        url=f"sqlite+pysqlite:///{database_path()}",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_sqlite_engine(database_path())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
