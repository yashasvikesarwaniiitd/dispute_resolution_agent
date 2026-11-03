# ADR 0008: Optimistic versions, BEGIN IMMEDIATE, an action claim, one worker process

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
Every step transaction saves the case with `expected_version` (UPDATE ... WHERE version = ?), runs as `BEGIN IMMEDIATE`, and is retried up to three times on conflict. External writes additionally take a lease on the action row with one conditional UPDATE. v1 runs one worker process with an in-memory per-case lock. SQLite runs with foreign keys on, WAL, synchronous FULL, busy_timeout 5000, STRICT tables, and version 3.37 or later.

## Why
Plainly: two things touching the same case at once must never both win, and nothing may be sent twice. SQLite allows one writer at a time; `BEGIN IMMEDIATE` takes that lock at the start so a transaction never fails late at commit. The version check protects the gap between reading a case and committing a step that made slow calls. The claim protects the one place where repeating work is harmful (the external write). A single worker keeps v1 simple; the database checks still make a second process safe.

## Options considered
- Pessimistic row locks: SQLite has none.
- A distributed lock or queue: unnecessary for one process (YAGNI).

## Consequences
Easier: conflicts are rare and handled by re-running a pure step. Harder: model calls may be repeated after a conflict (cost is recorded in `model_calls`). Watch: a conflict must never escalate a case; it is not the case's fault.

## Sources
Project documents. SQLite behaviour (STRICT tables, partial unique indexes, triggers, NULL handling in trigger conditions) was verified empirically against SQLite 3.45.1 in the A1 schema check (docs/spec/orchestrator.md appendix A). Not yet read: Phase 1 must read and link https://www.sqlite.org/stricttables.html, https://www.sqlite.org/partialindex.html, https://www.sqlite.org/lang_transaction.html and https://www.sqlite.org/wal.html before implementing.

## Implementation notes (Phase 1, 2026-11-03)

The decision held. `BEGIN IMMEDIATE` is applied by a SQLAlchemy `begin` event listener so no
call site can forget it, and the four pragmas are applied on every connection with
`verify_pragmas` failing startup if `foreign_keys` did not take — it is off by default in
SQLite, and without it every `REFERENCES` clause in the schema is decoration.

Optimistic versioning is implemented twice on purpose: `save` matches on the expected version
in its WHERE clause, and `tr_cases_version` refuses an update that does not increment by
exactly one. The first gives a useful error; the second catches a write that bypassed the
repository.

**The documentation links this ADR asked for are still outstanding.** The ADR says Phase 1
must read and link the SQLite pages on STRICT tables, partial indexes, transactions and WAL
before implementing, and I did not. What I did instead was verify each behaviour empirically
against the SQLite this project actually runs on: fifty constraint tests exercise STRICT
typing, the partial unique indexes on open cases, credits and active timers, all nine
triggers, and the NULL-handling in trigger conditions that the A1 schema check found (case
S21). The pragmas are asserted rather than assumed.

That is stronger evidence for this database than a document is, but it is not what the ADR
asked for, and the two are not interchangeable: empirical checks confirm what *does* happen
and documentation says what is *guaranteed* to keep happening. Reading and linking them
remains open, and `docs/BACKLOG.md` carries it.
