# ADR 0004: Step-driven orchestrator, one committed transition per step

Status: accepted (owner, 2026-11-02)
Date: 2026-11-02

## Decision
The orchestrator advances a case in steps. Each step reads, calls ports outside any transaction, then commits exactly one transition (or a rest) with all its rows and events in one unit of work, guarded by the case version.

## Why
In plain terms: if the program stops halfway, every case is left in a state it was actually in, never half-way between two. The one-shot `handle_event` sketch in FLOW.md section 3 would hold a lock across model and HTTP calls and could not be resumed after a crash mid-way. With one commit per step, a crash loses at most one uncommitted step, which is safe to re-run because external writes are protected by idempotency keys and lookup-before-write (ADR 0007).

## Options considered
- One transaction per event (the sketch): simplest to read, but long lock holds, and a failure after an email is sent but before commit could resend it.
- A job queue per step: more moving parts; needs an ADR showing the need (YAGNI, CLAUDE.md section 6).

## Consequences
Easier: restart recovery is "advance every case in an auto state". Every step has a clear test boundary. Harder: more transactions per case (cheap in SQLite). Watch: a step must never write outside its transaction; an architecture test checks that steps write only through the `UnitOfWork` passed to `commit`.

## Sources
Project documents only (CLAUDE.md, docs/ARCHITECTURE.md, docs/FLOW.md). No external documentation was needed.

## Implementation notes (Phase 1, 2026-11-03)

The decision held. Two things it did not say, which implementing it settled:

**The loop decides; the caller commits.** `Orchestrator.advance` returns the case and every
event it produced rather than writing them, and the unit of work is opened outside. That was
not in the ADR and it is what makes the whole loop testable without a database — the step
limit, the `ModuleUnavailable` conversion and the resting behaviour are all covered by tests
that touch no storage at all.

**One conversion point, enforced structurally.** The ADR says a step must never write outside
its transaction. The same reasoning applies to `ModuleUnavailable`: nine steps each turning
it into a review reason would eventually be eight doing it and one forgetting. There is one
`except ModuleUnavailable` in the orchestrator and a test counts them.

Phase 1's steps all escalate, because there is no pack or reasoner to decide anything yet.
That is a property of the phase, not of this decision.
