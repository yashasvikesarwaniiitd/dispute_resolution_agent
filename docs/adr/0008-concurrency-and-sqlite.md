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
