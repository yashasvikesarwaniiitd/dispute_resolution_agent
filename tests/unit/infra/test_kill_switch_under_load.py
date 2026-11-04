"""Bug fix (found by the end-to-end test): the kill switch must be readable while a case's
write transaction is open. It used to wait for that transaction, time out and fail safe, so
the running app refused every credit and email as `kill_switch_active`."""

from __future__ import annotations

import time
from datetime import UTC, datetime

from sqlalchemy import text

from dispute_agent.infra.sqlite.control_flags import SqliteKillSwitch
from tests.support.patterns import new_engine

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def test_it_reads_off_while_a_write_transaction_is_open() -> None:
    engine = new_engine()
    switch = SqliteKillSwitch(engine)
    with engine.begin() as conn:  # BEGIN IMMEDIATE: holds the write lock, like a case step
        conn.execute(text("SELECT 1"))
        started = time.monotonic()
        assert switch.is_engaged() is False
        assert time.monotonic() - started < 2  # answered, not timed out


def test_it_still_reads_on_when_thrown_and_off_when_released() -> None:
    engine = new_engine()
    switch = SqliteKillSwitch(engine)
    switch.set(True, "human:owner", NOW)
    with engine.begin() as conn:
        conn.execute(text("SELECT 1"))
        assert switch.is_engaged() is True
    switch.set(False, "human:owner", NOW)
    assert switch.is_engaged() is False


def test_the_setting_still_forces_it_on() -> None:
    assert SqliteKillSwitch(new_engine(), forced=True).is_engaged() is True
