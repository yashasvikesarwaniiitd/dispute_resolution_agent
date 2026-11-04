"""The kill switch, as a row (schema.sql `control_flags`, I10).

A row rather than a setting in memory, so every process sees the same switch and turning it on
needs no restart. Read on every check: the executor asks just before it writes, and a switch
thrown a moment earlier must win. A row that cannot be read counts as ON - not knowing whether
writes are allowed is a reason not to write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

from dispute_agent.infra.sqlite.mapping import to_iso

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Engine


class SqliteKillSwitch:
    """Implements KillSwitch."""

    def __init__(self, engine: Engine, *, forced: bool = False) -> None:
        self._engine = engine
        #: `KILL_SWITCH=true` in the settings (config.md, the port contract): on, whatever
        #: the row says. Either source can stop writes; neither can start them.
        self._forced = forced

    def is_engaged(self) -> bool:
        if self._forced:
            return True
        try:
            with self._engine.connect() as conn:
                row = conn.execute(
                    text("SELECT value FROM control_flags WHERE name = 'kill_switch'")
                ).first()
        except Exception:  # unreadable: fail safe (I10)
            return True
        return row is not None and str(row[0]) == "1"

    def set(self, on: bool, by: str, now: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO control_flags (name, value, updated_at, updated_by) "
                    "VALUES ('kill_switch', :v, :at, :by) "
                    "ON CONFLICT(name) DO UPDATE SET value = :v, updated_at = :at, updated_by = :by"
                ),
                {"v": "1" if on else "0", "at": to_iso(now), "by": by},
            )


__all__ = ["SqliteKillSwitch"]
