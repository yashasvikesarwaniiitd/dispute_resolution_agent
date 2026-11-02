# Stage A1: Kernel specification (Opus)

## Role
Principal architect producing a specification so complete that a smaller model can implement it without making design decisions.

## Context
No code exists. Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/FLOW.md` and `docs/HANDOFF.md`. This stage writes documents in `docs/spec/`, not code.

## Objective
An unambiguous, internally consistent specification of the kernel: interfaces, schema, state machine, events, orchestrator and executor.

## Scope
### In scope
Kernel interfaces, database schema, state machine as data, event catalog, orchestrator algorithm, executor lifecycle.
### Out of scope
Adapters, domain pack rules, prompts, Foundry (later stages).

## Tasks
1. `docs/spec/kernel-interfaces.md`: exact Python signatures (Protocols and frozen pydantic models) for every port and domain object, with docstrings, typed errors, and pre and postconditions per method.
2. `docs/spec/schema.sql`: full DDL with indexes and constraints: dedupe key unique, one open case per shipment and claim, idempotency key unique, `version` column, append-only `events`. Note SQLite specifics.
3. `docs/spec/transitions.yaml`: the state machine as data (from, to, trigger, guard, reopen paths). Reconcile with `docs/FLOW.md` and list every discrepancy with your decision and reason.
4. `docs/spec/events-catalog.md`: every event type with payload schema, cause, emitter, dedupe key and correlation id rules.
5. `docs/spec/orchestrator.md`: the handle-event algorithm with concurrency (optimistic version and retry), timers, restart recovery, `ModuleUnavailable` mapping and the three cross-case hooks. Include Mermaid sequence diagrams.
6. `docs/spec/executor.md`: action lifecycle, idempotency key derivation, read-back verification per action type, a failure-mode table (timeout, 4xx, 5xx, duplicate, partial) and retry policy.
7. A traceability table mapping each invariant I1 to I15 to the mechanism and a test idea.
8. ADRs for every decision you made that the existing docs left open.

## Technical requirements
No "TBD" or "to be decided" in any spec. Where a real choice remains, decide, record the reason, and record the main alternative.

## Acceptance criteria
- Every port method and domain field has a type, a meaning and an error behaviour.
- The state machine in `transitions.yaml` has no unreachable states and no transition that breaks an invariant (state the check you ran).
- A reviewer can write failing tests for Phase 1 from these documents alone.
- Every invariant has a mechanism and a test idea.

## Deliverables
Files in `docs/spec/`, ADRs, an update to `docs/HANDOFF.md` and `docs/PROGRESS.md`.

## Constraints
Do not weaken an invariant to make a design simpler. If one seems wrong, say so and propose a change for the owner to approve.

## Process
Outline first and wait for approval. Then write one document at a time. After each, list what a reviewer should check.

## Model and effort
Opus. Effort medium; use high for tasks 3 and 6. No `max`.

## Stop and ask if
An invariant conflicts with a requirement in the owner's brief.
