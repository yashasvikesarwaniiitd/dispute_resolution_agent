# ADR 0006: Transactional inbox for inputs, outbox for outcomes

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
Every inbound event is first stored in an `inbox` table, unique on its dedupe key, then dispatched. Case outcomes for the cross-case observer are written to an `outcome_outbox` table in the same transaction as the terminal transition and delivered at least once after commit.

## Why
The inbox gives three things at once: duplicate suppression ("the same event twice makes one case and one event"), a place for events that must wait (DEFERRED) or cannot be routed (UNROUTABLE), and restart safety (PENDING rows are re-dispatched). The audit trail stays append-only because processing status lives in the inbox, not on events. The outcome outbox makes HOOK C reliable without letting an observer failure touch the case, which Phase 7 needs without kernel changes.

## Options considered
- Dedupe on `events.dedupe_key` only: cannot represent deferred or unroutable input without mutating the audit trail.
- Calling the observer inline after commit: a crash between commit and call loses the outcome.

## Consequences
Easier: replay and recovery are queries. Harder: two extra tables. Watch: observers must be idempotent on `outcome.id` (contract test).

## Sources
Project documents only (docs/ARCHITECTURE.md sections 8-9, docs/FLOW.md section 6). No external documentation was needed.
