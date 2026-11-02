# Phase 1: Kernel

## Role
Principal engineer building the safety core that every later phase depends on.

## Context
Phase 0 is done: ports, registry and tooling exist. Read `docs/FLOW.md` sections 1 to 4 and the transition table.

## Objective
A kernel that opens one persistent case per dispute, moves it only along legal transitions, logs every change, and runs an orchestrator skeleton end to end with fake ports and no AI.

## Scope
### In scope
Persistence, state machine, intake with dedupe, policy engine with its first rules, executor skeleton, timers table, kill switch, orchestrator skeleton, `/events` and `/cases/{id}` endpoints.
### Out of scope
Domain calculations, real channels, LLM, ledger, approvals UI, cross-case behaviour.

## Tasks
1. SQLAlchemy Core tables and the first Alembic migration for `cases`, `events`, `evidence_items`, `proposals`, `approvals`, `actions`, `messages`, `timers`. SQLite repositories behind `CaseRepository` and `EventLog`, with optimistic versioning.
2. The declarative transition table in `kernel/statemachine/transitions.py` and a `StateMachine.apply` that refuses illegal moves and always emits an event with a cause. Write the table from `docs/FLOW.md`; list any inconsistency in the plan.
3. Intake: `open_or_get_case(event)` deduplicating on `<source>:<source_event_id>` and one open case per shipment and claim. Return the existing case on a repeat.
4. `PolicyEngine` with composable rules: legal transition, kill switch, contact limits, module availability, approval-required-for-credit, approval-required-for-message-kind (kinds listed in `policy.yaml`). Plus the caution rule: `CautionSignal`s may only escalate a verdict (Allow to RequireApproval or Escalate; never the reverse).
5. `ActionExecutor` skeleton: PLANNED, SENT, VERIFIED, FAILED, UNKNOWN; idempotency keys; kill switch checked inside; unknown becomes `NEEDS_HUMAN_REVIEW`. Test it against an in-memory fake adapter.
6. Orchestrator skeleton following `docs/FLOW.md` section 3, with the three cross-case hooks present and wired to Null adapters, and `ModuleUnavailable` converted to `NEEDS_HUMAN_REVIEW`.
7. Timers: schedule, fire and re-load on start-up. A restart test must resume a waiting case.
8. FastAPI routes `POST /events` and `GET /cases/{id}` (state, events). Structured logs with `case_id` and `correlation_id`.
9. ADRs for state-machine design, idempotency and outbox, and the optimistic concurrency choice.

## Technical requirements
- Property-based tests (hypothesis) over random event sequences: no illegal state is ever reachable; terminal states stay terminal except through the documented reopen paths.
- Branch coverage of `kernel/` at or above 90%.

## Acceptance criteria
- An illegal transition is refused and nothing is written (unit test).
- The same event sent twice produces one case and one event (API test).
- Killing and restarting the process mid-wait resumes the case and fires its timer (restart test).
- A `CautionSignal` cannot turn a Deny or RequireApproval verdict into Allow (property test).
- Disabling the `email` module and driving a case to an email step ends in `NEEDS_HUMAN_REVIEW` with reason `module_unavailable:email`.
- An UNKNOWN action result never leads to a resolved outcome.
- A message of a review-required kind with no approval record is refused by policy, even if the executor is called directly (test).

## Deliverables
Migrations, kernel code, tests, ADRs, updated `docs/PROGRESS.md`.

## Constraints
I2, I4, I6, I7, I8, I10 are all exercised here. `kernel` stays free of third-party I/O libraries.

## Process
Plan mode first. Write the transition table and the policy rule list in the plan, so the owner can review them before code.

## Model and effort
Plan in Opus at effort high (state machine, policy, executor are safety-critical); build on Sonnet. Run the `safety-reviewer` subagent (Opus) on the state machine, policy engine and executor before finishing. Escalate any invariant doubt to Opus.

## Stop and ask if
The transition table in `docs/FLOW.md` allows a path that breaks an invariant.
