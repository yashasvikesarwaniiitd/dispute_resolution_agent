"""The cross-case tables (Phase 7, P7-01).

Revision ID: 0002_patterns
Revises: 0001_initial
Create Date: 2026-11-04

Executes `infra/sqlite/patterns.py` PATTERNS_DDL verbatim, for the same reason 0001 executes
schema.sql: the constraints are the part that matters, and a transcription could weaken one
without any test noticing. schema.sql itself is frozen and is not edited.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

from dispute_agent.infra.sqlite.patterns import PATTERNS_DDL, TABLES

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0002_patterns"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.get_bind().connection.executescript(PATTERNS_DDL)


def downgrade() -> None:
    connection = op.get_bind()
    for table in TABLES:
        connection.exec_driver_sql(f"DROP TABLE IF EXISTS {table}")
