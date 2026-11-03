"""One step, one transaction (kernel-interfaces.md 6.2, ADR 0004, ADR 0008).

Everything the kernel writes for one step — the case row, its events, intents, actions,
timers, inbox status — commits together or not at all. That is what makes a crash survivable:
the system comes back to a state a case was actually in, never half-way between two, and the
step can simply be run again because external writes are protected by idempotency keys.

The transaction is BEGIN IMMEDIATE, so the write lock is taken before the first read rather
than at the first write. Without that, two handlers can both read a case at version 3, both
decide what to do, and only collide at commit — by which point one of them may already have
sent an email.

Phase 1 wires the stores it needs. The rest of the ports named in section 6.2 arrive with the
cards that use them, and the protocol is unchanged when they do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from dispute_agent.infra.sqlite.case_repository import SqliteCaseRepository
from dispute_agent.infra.sqlite.event_log import SqliteEventLog
from dispute_agent.kernel.domain.errors import ConcurrencyConflict

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType

    from sqlalchemy import Connection
    from sqlalchemy.engine import Engine

    from dispute_agent.kernel.domain.ids import CaseId


class SqliteUnitOfWork:
    """Implements UnitOfWork.

    Used as a context manager. Leaving it without an exception commits; leaving it with one
    rolls back, which is why a step that raises half-way through cannot leave a case row
    saying one thing and its events saying another.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._connection: Connection | None = None
        self._last_save: tuple[CaseId | None, int] = (None, 0)
        self.cases: SqliteCaseRepository
        self.events: SqliteEventLog

    def __enter__(self) -> Self:
        self._connection = self._engine.connect()
        # SQLAlchemy begins implicitly; the engine's "begin" listener makes it IMMEDIATE.
        self._connection.begin()
        self.cases = SqliteCaseRepository(self._connection, on_save=self.note_save)
        self.events = SqliteEventLog(self._connection)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        connection = self._connection
        if connection is None:
            return
        try:
            if exc_type is None:
                try:
                    connection.commit()
                except Exception as commit_error:
                    connection.rollback()
                    raise self._translate(commit_error) from commit_error
            else:
                connection.rollback()
        finally:
            connection.close()
            self._connection = None

    def _translate(self, error: Exception) -> Exception:
        """A commit that failed on a version check is a concurrency conflict, not a crash.

        The caller's answer is different: a conflict means re-read and run the step again,
        which is safe because external writes carry idempotency keys. Anything else means
        something is wrong that retrying will not fix.

        In practice `CaseRepository.save` catches the common case first, by matching on the
        version in its WHERE clause. This covers the rest: a write that reached the database
        and was refused by `tr_cases_version`.
        """
        text = str(error)
        if "version_must_increment_by_one" in text or "UNIQUE" in text.upper():
            case_id, version = self._last_save
            if case_id is not None:
                return ConcurrencyConflict(case_id, version)
        return error

    def note_save(self, case_id: CaseId, expected_version: int) -> None:
        """Remembered so a commit-time conflict can name the case it was about."""
        self._last_save = (case_id, expected_version)

    @property
    def connection(self) -> Connection:
        if self._connection is None:
            raise RuntimeError("the unit of work is not open")
        return self._connection


def unit_of_work_factory(engine: Engine) -> Callable[[], SqliteUnitOfWork]:
    """A `UnitOfWorkFactory`: the kernel asks for a unit of work without knowing the engine."""

    def factory() -> SqliteUnitOfWork:
        return SqliteUnitOfWork(engine)

    return factory
