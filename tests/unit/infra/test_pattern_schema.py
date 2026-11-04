"""P7-01: the cross-case tables refuse what they are supposed to refuse.

Same shape as test_schema_constraints: start from a row the database accepts, change one
thing, and assert the refusal - so each test can only pass because of the constraint it names.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from dispute_agent.infra.sqlite.patterns import PATTERNS_DDL, STATUSES
from tests.unit.infra.conftest import LATER, SCHEMA, TS, a_case, insert


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.executescript(PATTERNS_DDL)
    conn.execute("PRAGMA foreign_keys = ON")
    insert(conn, "cases", a_case())
    return conn


def a_pattern(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "pat_01HQ",
        "dimension": "scanner",
        "match_value": "SCN-03-2",
        "status": "UNCONFIRMED",
        "summary": "5 disputes in 10 days were measured by scanner SCN-03-2.",
        "summary_source": "code",
        "thresholds_json": '{"min_related_cases": 3, "window_days": 14}',
        "status_reason": "cluster_found",
        "first_seen_at": TS,
        "last_seen_at": LATER,
        "created_at": TS,
        "updated_at": TS,
    }
    row.update(overrides)
    return row


def a_link(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "pattern_id": "pat_01HQ",
        "case_id": "case_01HQ",
        "role": "member",
        "linked_at": TS,
    }
    row.update(overrides)
    return row


def refuses() -> Any:
    return pytest.raises((sqlite3.IntegrityError, sqlite3.OperationalError))


class TestPatternRecords:
    @pytest.mark.parametrize("status", STATUSES)
    def test_each_of_the_four_statuses_is_accepted(
        self, db: sqlite3.Connection, status: str
    ) -> None:
        fixed = LATER if status == "FIXED_MONITORING" else None
        insert(db, "pattern_records", a_pattern(status=status, fixed_at=fixed))

    def test_any_other_status_is_refused(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(status="PROVEN"))

    def test_fixed_monitoring_needs_the_fix_date(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(status="FIXED_MONITORING"))

    def test_the_rule_that_produced_it_is_recorded(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(thresholds_json="not json"))

    def test_the_summary_is_capped_at_400(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(summary="x" * 401))

    def test_one_pattern_per_cluster_key(self, db: sqlite3.Connection) -> None:
        insert(db, "pattern_records", a_pattern())
        with refuses():
            insert(db, "pattern_records", a_pattern(id="pat_02HQ"))

    def test_last_seen_cannot_precede_first_seen(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(first_seen_at=LATER, last_seen_at=TS))

    def test_an_unknown_dimension_is_refused(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_records", a_pattern(dimension="weather"))


class TestPatternCaseLinks:
    def test_a_link_to_a_real_pattern_and_case_is_accepted(self, db: sqlite3.Connection) -> None:
        insert(db, "pattern_records", a_pattern())
        insert(db, "pattern_case_links", a_link())
        insert(db, "pattern_case_links", a_link(role="informed"))

    def test_a_link_needs_a_pattern(self, db: sqlite3.Connection) -> None:
        with refuses():
            insert(db, "pattern_case_links", a_link())

    def test_a_link_needs_a_case(self, db: sqlite3.Connection) -> None:
        insert(db, "pattern_records", a_pattern())
        with refuses():
            insert(db, "pattern_case_links", a_link(case_id="case_missing"))

    def test_the_role_is_constrained(self, db: sqlite3.Connection) -> None:
        insert(db, "pattern_records", a_pattern())
        with refuses():
            insert(db, "pattern_case_links", a_link(role="culprit"))


class TestFindingsAndOutcomes:
    def test_a_finding_verdict_is_constrained(self, db: sqlite3.Connection) -> None:
        insert(db, "pattern_records", a_pattern())
        row = {
            "pattern_id": "pat_01HQ",
            "finding_ref": "WD-7",
            "verdict": "seller_guilty",
            "recorded_at": TS,
        }
        with refuses():
            insert(db, "pattern_findings", row)

    def test_an_outcome_is_recorded_once(self, db: sqlite3.Connection) -> None:
        row = {
            "outcome_id": "out_01",
            "case_id": "case_01HQ",
            "state": "WITHDRAWN",
            "resolved": 0,
            "closed_at": TS,
            "observed_at": TS,
        }
        insert(db, "pattern_outcomes", row)
        with refuses():
            insert(db, "pattern_outcomes", row)
