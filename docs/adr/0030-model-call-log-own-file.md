# 0030 — The model-call log lives in its own database file

Date: 2026-11-04
Status: accepted (Phase 4P, built under the owner's instruction of 2026-11-04 to build without questions)

## Decision

`model_calls` is written to `data/model_calls.sqlite3`, not to the case database. Each row is
committed on its own, the moment the call ends. The table is schema.sql's own DDL, minus the
foreign key to `cases`.

## Why

A model call happens while the case database holds its write lock (`BEGIN IMMEDIATE`, ADR 0008).
Two options were wrong:

- **Same file, separate connection**: waits on the case's lock, then fails with "database is locked".
- **Same file, the case's own connection**: the row rolls back if the case transaction fails later,
  so a call that was billed disappears from the spend the cap reads.

The spending cap (ADR 0017) is only as good as the record it reads, so the record must not depend
on anything that happens after the call. The ledger already sets this precedent (`ledger.sqlite3`).

## Consequences

- Spend is never lost to a rolled-back case.
- `case_id` is not an enforced reference; a row can name a case whose transaction rolled back.
  That is the truth (the call happened), not an inconsistency.
- Cost is stored in micro-dollars rounded up, so the recorded spend never drifts below the bill.
