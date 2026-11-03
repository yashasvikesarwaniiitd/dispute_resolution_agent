"""A real database built from the reference DDL, plus valid rows to break.

Every fixture here produces a row that the schema *accepts*. The tests then change one field
and assert the database refuses it. Starting from a valid row matters: a test that inserts
something broken in two ways can pass because of the wrong constraint, and would keep passing
after the constraint it claims to test was deleted.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCHEMA = (ROOT / "docs" / "spec" / "schema.sql").read_text(encoding="utf-8")

TS = "2026-03-01T09:30:00.000000Z"
LATER = "2026-03-02T09:30:00.000000Z"
HASH = "sha256:" + "a" * 64
KEY = "wd1:" + "b" * 64


@pytest.fixture
def db() -> sqlite3.Connection:
    """An in-memory database with the schema and foreign keys actually on."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys = ON")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, (
        "foreign keys are off; every REFERENCES clause would be decoration"
    )
    return conn


def insert(conn: sqlite3.Connection, table: str, row: dict[str, Any]) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(row.values()))


def a_case(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "case_01HQ",
        "pack": "weight_dispute",
        "shipment_ref": "SHP-0001",
        "claim_ref": "CHG-0001",
        "seller_ref": "SELLER-0001",
        "seller_contact": "seller@example.invalid",
        "facts_json": "{}",
        "state": "RECEIVED",
        "version": 1,
        "correlation_id": "corr_01HQ",
        "opened_at": TS,
        "updated_at": TS,
        "state_entered_at": TS,
    }
    row.update(overrides)
    return row


def an_event(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "evt_01HQ",
        "case_id": "case_01HQ",
        "seq": 1,
        "type": "DISPUTE_RAISED",
        "category": "INPUT",
        "occurred_at": TS,
        "recorded_at": TS,
        "actor_kind": "EXTERNAL",
        "actor_id": "dispute_api",
        "cause_kind": "INPUT_EVENT",
        "cause_ref": "inb_01HQ",
        "correlation_id": "corr_01HQ",
        "dedupe_key": "dispute_api:form-1",
        "payload_json": "{}",
    }
    row.update(overrides)
    return row


def an_intent(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "int_01HQ",
        "case_id": "case_01HQ",
        "source_kind": "PROPOSAL",
        "source_ref": "prp_01HQ",
        "action_type": "SEND_MESSAGE",
        "message_kind": "evidence_request",
        "payload_json": "{}",
        "payload_hash": HASH,
        "status": "PLANNED",
        "created_at": TS,
    }
    row.update(overrides)
    return row


def an_approval(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "apr_01HQ",
        "case_id": "case_01HQ",
        "intent_id": "int_01HQ",
        "subject": "MESSAGE",
        "payload_hash": HASH,
        "message_kind": "evidence_request",
        "requested_at": TS,
        "expires_at": LATER,
        "decision": "APPROVED",
        "decided_by": "human:owner",
        "decided_at": TS,
    }
    row.update(overrides)
    return row


def an_action(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "act_01HQ",
        "case_id": "case_01HQ",
        "intent_id": "int_01HQ",
        "action_type": "SEND_MESSAGE",
        "action_seq": 1,
        "idempotency_key": KEY,
        "payload_hash": HASH,
        "status": "PLANNED",
        "planned_at": TS,
    }
    row.update(overrides)
    return row


def a_timer(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "tmr_01HQ",
        "case_id": "case_01HQ",
        "kind": "SELLER_REMINDER",
        "due_at": LATER,
        "anchor_at": TS,
        "armed_in_state": "AWAITING_SELLER",
        "armed_at_version": 1,
        "correlation_id": "corr_01HQ",
        "created_at": TS,
    }
    row.update(overrides)
    return row
