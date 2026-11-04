"""A small cross-case world for the Phase 7 tests: a real database, cases and patterns."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.mapping import to_iso
from dispute_agent.infra.sqlite.patterns import apply_pattern_schema
from dispute_agent.kernel.domain.case import CaseFacts

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository

T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
THRESHOLDS = {"min_related_cases": 3, "window_days": 14}


def new_engine() -> Engine:
    path = Path(tempfile.mkdtemp(prefix="patterns-")) / "cases.sqlite3"
    engine = create_sqlite_engine(path)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    apply_pattern_schema(engine)
    return engine


def add_case(
    engine: Engine,
    case_id: str,
    *,
    claim: str | None = None,
    seller: str = "SELL-0001",
    shipment: str | None = None,
    opened_at: datetime = T0,
) -> None:
    claim = claim or f"CHG-{case_id}"
    shipment = shipment or f"SHP-{case_id}"
    ts = to_iso(opened_at)
    row: dict[str, Any] = {
        "id": case_id,
        "pack": "weight_dispute",
        "shipment_ref": shipment,
        "claim_ref": claim,
        "seller_ref": seller,
        "seller_contact": "seller@example.invalid",
        "facts_json": "{}",
        "state": "RECEIVED",
        "version": 1,
        "correlation_id": f"corr_{case_id}",
        "opened_at": ts,
        "updated_at": ts,
        "state_entered_at": ts,
    }
    raw = engine.raw_connection()
    try:
        cols = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        raw.execute(f"INSERT INTO cases ({cols}) VALUES ({marks})", tuple(row.values()))
        raw.commit()
    finally:
        raw.close()


def add_pattern(
    repo: PatternRepository,
    pattern_id: str,
    dimension: str,
    value: str,
    members: list[str],
    *,
    seen: datetime = T0,
    status: str = "UNCONFIRMED",
) -> None:
    repo.create(
        pattern_id,
        dimension=dimension,
        value=value,
        summary=f"{len(members)} disputes share {dimension} {value}.",
        thresholds=THRESHOLDS,
        first_seen_at=seen,
        last_seen_at=seen,
        now=seen,
    )
    repo.link(pattern_id, members, "member", seen)
    if status != "UNCONFIRMED":
        fixed = seen + timedelta(days=1) if status == "FIXED_MONITORING" else None
        repo.set_status(pattern_id, status, "test", seen, fixed_at=fixed)


def facts(
    claim: str = "CHG-now", seller: str = "SELL-0009", shipment: str = "SHP-now"
) -> CaseFacts:
    return CaseFacts(
        shipment_ref=shipment,
        claim_ref=claim,
        seller_ref=seller,
        seller_contact="seller@example.invalid",
        raised_at=T0,
    )
