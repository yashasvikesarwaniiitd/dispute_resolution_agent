"""The kernel schema, applied verbatim from docs/spec/schema.sql.

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-03

Why this executes the reference DDL rather than building the schema in SQLAlchemy Core:
schema.sql is the authority (its own header says so), and the parts of it that carry the
safety invariants are the parts Core expresses least well — eight triggers, four partial
unique indexes, and CHECK constraints that span columns. `ux_actions_one_credit` (I3) and
`tr_actions_status` (I4) are two lines of SQL each and would be a paragraph of Python with a
dialect-specific `sqlalchemy.Index(..., sqlite_where=...)` that nobody would read again.

Transcribing 534 lines of DDL is 534 chances to weaken a constraint by accident, and the
weakened one would not fail any test — it would simply stop refusing something. Executing the
file verbatim means there is no transcription to get wrong.

`tests/unit/infra/test_schema_conformance.py` holds `tables.py` to the result, so the Core
definitions used for querying cannot drift from what was actually created.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

from dispute_agent.infra.sqlite.engine import schema_sql_path

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Dropped in reverse dependency order. Triggers and indexes go with their tables.
TABLES: tuple[str, ...] = (
    "model_calls",
    "control_flags",
    "outcome_outbox",
    "timers",
    "messages",
    "actions",
    "approvals",
    "policy_verdicts",
    "action_intents",
    "proposals",
    "assessments",
    "evidence_items",
    "events",
    "inbox",
    "cases",
    "legal_transitions",
)


def upgrade() -> None:
    sql = schema_sql_path().read_text(encoding="utf-8")
    connection = op.get_bind()
    raw = connection.connection
    raw.executescript(sql)


def downgrade() -> None:
    connection = op.get_bind()
    # The no-delete triggers on cases and events refuse DELETE, not DROP, so the tables go
    # cleanly. Order matters because foreign keys are on.
    for table in TABLES:
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
