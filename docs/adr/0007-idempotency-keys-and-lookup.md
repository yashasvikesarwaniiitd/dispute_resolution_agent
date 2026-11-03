# ADR 0007: Idempotency keys include the action sequence; every write port supports lookup by key

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
Key = `"wd1:" + sha256(case_id | action_type | action_seq | payload_hash)`, computed once at planning and stored on the action. Every write port (`MessageChannel`, `TicketingSystem`, `Ledger`) adds a `find_*` method by key and declares `idempotent_writes` and `lookup_supported`. The executor looks up by key before every write.

## Why
The ARCHITECTURE.md sketch (`case_id | action_type | payload_hash`) gives two separate but identical actions (the same evidence request after a reopen) the same key, so the second would be swallowed as a "duplicate" while the case believes it was sent. Adding the per-case action sequence fixes that; a retry still reuses the stored key. Lookup-before-write is what makes retries and crash recovery safe for systems that are not natively idempotent (sending email is not): if we cannot prove an earlier attempt did not happen, the result is UNKNOWN and a human decides, never a second send.

## Options considered
- A random key per action row: equally safe for retries, but not reproducible in tests and loses the payload binding.
- Trusting adapters to be idempotent: false for email.

## Consequences
Easier: crash recovery is uniform across systems. Harder: each adapter must implement a lookup (Stage A3 decides how: message header search, Jira label, ledger key). If an adapter cannot, it sets `lookup_supported = False` and more outcomes become UNKNOWN; that is the honest result.

## Sources
Project documents only (docs/ARCHITECTURE.md section 9). Per-system lookup feasibility is verified against official documentation in Stage A3.

## Implementation notes (Phase 1, 2026-11-03)

The decision held exactly as written. The key is derived once in `kernel/executor/keys.py`,
stored on the action row, and read back on every retry — never recomputed, so a change to the
formula cannot turn a retry into a second write. `wd1:` versions the derivation.

The `action_seq` argument earned its place in a test rather than in theory: without it, the
same evidence request sent again after a reopen produces the same key as the first, and the
second send is silently swallowed as a duplicate.

Lookup-before-write is implemented in `execute` and is the crash-after-write path. The test
that proves it resets a sent action to PLANNED and runs `execute` again: the port is called
once in total, and the second run reports `found_by_lookup`.

One thing the ADR implies and the code now states: `TransientPortError` and `AmbiguousOutcome`
must stay separate types. The first means the call certainly did not take effect and can be
failed safely; the second means it may have, and becomes UNKNOWN. Collapsing them would make
every timeout either a lost credit or a double-posted one.
