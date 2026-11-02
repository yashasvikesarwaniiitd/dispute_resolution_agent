# Phase 1 task cards — Kernel

Stage A5. Source prompt: `docs/build-prompts/phase-1-kernel.md`. Phase outcome: a kernel that opens one persistent case per dispute, moves it only along legal transitions, logs every change, and runs an orchestrator skeleton end to end with fake ports and no AI.

**Phase defaults:** Sonnet, effort medium. **This is a safety-critical phase** (`CLAUDE.md` section 17): run the `safety-reviewer` subagent before closing it, and the cards marked **Opus review** below are the ones that implement an invariant's enforcement point. Branch coverage of `kernel/` must reach 90% by the phase exit.

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P1-01 | Tables and the first migration | L | P0-* | |
| P1-02 | SQLite repositories with optimistic versioning | M | P1-01 | |
| P1-03 | The transition table as data | L | P0-03 | yes |
| P1-04 | `StateMachine.apply` and the event acceptance matrix | L | P1-03 | yes |
| P1-05 | Intake and deduplication | M | P1-02, P1-04 | |
| P1-06 | `PolicyEngine` and composable rules | L | P1-04 | yes |
| P1-07 | The caution rule | S | P1-06 | yes |
| P1-08 | `ActionExecutor` skeleton | L | P1-06 | yes |
| P1-09 | Orchestrator skeleton and the cross-case hooks | L | P1-06, P1-08 | |
| P1-10 | Timers and restart recovery | M | P1-02, P1-04 | |
| P1-11 | API routes and structured logs | S | P1-05, P1-09 | |
| P1-12 | Property-based state machine tests | M | P1-04 | |
| P1-13 | Resolve the T90 open item | S | P1-03 | |
| P1-14 | ADRs | S | P1-08 | |

---

## P1-01 — Tables and the first migration

**Files:** `src/dispute_agent/infra/sqlite/tables.py`, `migrations/versions/0001_initial.py`.

**Implements:** `schema.sql` in full — it is the authoritative DDL, including every CHECK constraint, partial unique index and trigger.

**Build:** SQLAlchemy Core tables for `cases`, `events`, `evidence_items`, `proposals`, `approvals`, `actions`, `messages`, `timers`, plus everything else `schema.sql` declares. Transcribe the constraints; they are not decoration. Named ones that later invariants rest on: `ux_actions_one_credit` (I3), `CHECK (action_type <> 'POST_CREDIT' OR approval_id IS NOT NULL)` (I3), `approvals.used_by_action_id UNIQUE` (I3), `tr_actions_status` (I4), the one-active-timer-per-case-and-kind partial index, and `timers.gather_round` with its `CHECK ((gather_round IS NOT NULL) = (kind = 'EVIDENCE_RETRY'))` (D41).

**Tests first:** `tests/unit/infra/test_schema_constraints.py` — one test per constraint, each inserting the row the constraint must refuse and asserting the database rejects it. `schema.sql` carries numbered schema checks (S1-S27); cover each.

**Acceptance:** `alembic upgrade head` on an empty file; `pytest tests/unit/infra/test_schema_constraints.py -q`.

**Size:** L. **Model:** Sonnet, effort medium.

**Watch:** SQLite enforces CHECK and UNIQUE but needs `PRAGMA foreign_keys=ON` per connection. Set it in the engine, and test that a bad foreign key is actually refused — a silently ignored pragma makes several later tests pass for the wrong reason.

---

## P1-02 — SQLite repositories with optimistic versioning

**Files:** `src/dispute_agent/infra/sqlite/{case_repository,event_log,unit_of_work}.py`.

**Implements:** `kernel-interfaces.md` (`CaseRepository`, `EventLog`), ADR 0008 (optimistic versions, `BEGIN IMMEDIATE`, one worker process).

**Build:** `save(case, expected_version)` raises on a version mismatch rather than overwriting. `EventLog.append` is append-only — no update or delete path exists on the port or the implementation. Transactions use `BEGIN IMMEDIATE` (ADR 0008) so two writers cannot interleave a read-modify-write.

**Tests first:** `tests/unit/infra/test_optimistic_versioning.py` — two readers load version 3, both save; the second raises. `test_event_log_append_only.py` — the port exposes no mutation; a direct UPDATE on `events` is refused by the trigger.

**Acceptance:** `pytest tests/unit/infra -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P1-03 — The transition table as data

**Files:** `src/dispute_agent/kernel/statemachine/transitions.py`.

**Implements:** `transitions.yaml` in full — states, groups, timer kinds, triggers, guards, steps, transitions, the accept matrix, and checks C1-C16.

**Build:** load or transcribe `transitions.yaml` into frozen data. The table is **data, not code** (ADR 0005, `CLAUDE.md` section 6): no `if` statement anywhere decides whether a move is legal. Every transition keeps its id (T00-T90), guards and effects, so an event row can name the transition that caused it.

**Tests first:** `tests/unit/statemachine/test_table_matches_spec.py` — the table in code equals `docs/spec/transitions.yaml`, field for field, read from the file at test time. This is the test that stops the two drifting. Plus checks C1-C16 as named tests.

**Acceptance:** `pytest tests/unit/statemachine/test_table_matches_spec.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes — this table is the backbone every later invariant rests on.

**Watch:** `T90` uses `from_group: escalable`, which expands to nine states. Expand it at load time, not at check time, so the legal-edge set is explicit and testable. `scripts/check_scenarios.py` already does exactly this expansion; reuse its reading of the file rather than writing a second interpretation.

---

## P1-04 — `StateMachine.apply` and the event acceptance matrix

**Files:** `src/dispute_agent/kernel/statemachine/machine.py`.

**Implements:** `transitions.yaml` (`accept`), `events-catalog.md`, ADR 0004 (one committed transition per step), ADR 0005, I8.

**Build:** `apply(case, trigger, cause)` returns the new state and the event to append, or raises `IllegalTransition` / `GuardFailed`. **Every** state change writes an event carrying its cause (I8) — there is no code path that changes state without one. The acceptance matrix decides which inbound events a case in a given state will even consider; a rejected event is recorded, not dropped silently.

**Tests first:**
- `tests/unit/statemachine/test_apply.py` — every legal transition applies; a sample of illegal ones raise.
- `test_accept_matrix.py` — for each (state, event type) the matrix's verdict is honoured.
- `test_every_change_emits_event.py` — drive 50 random legal sequences; assert `len(events) == len(state_changes)`.

**Acceptance:** `pytest tests/unit/statemachine -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I8).

---

## P1-05 — Intake and deduplication

**Files:** `src/dispute_agent/kernel/orchestrator/intake.py`.

**Implements:** `orchestrator.md` (intake), ADR 0006 (transactional inbox), ADR 0007 (idempotency keys).

**Build:** `open_or_get_case(event)` deduplicates on `<source>:<source_event_id>` and enforces one open case per shipment and claim. A repeat returns the existing case — it does not create, and it does not raise. Intake refuses a dispute whose seller contact is not on the test allowlist (I11, scenario S27: HTTP 422, no inbox row, no case, no event).

**Tests first:** `tests/unit/orchestrator/test_intake.py` — the same event twice yields one case and one input event (scenario S11); a second dispute for the same shipment and claim while one is open returns the open case; a non-allowlisted seller is refused with no row written.

**Acceptance:** `pytest tests/unit/orchestrator/test_intake.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P1-06 — `PolicyEngine` and composable rules

**Files:** `src/dispute_agent/kernel/policy/{engine,verdicts}.py`, `kernel/policy/rules/`, `config/policy.yaml`.

**Implements:** `policy-rules.md` (R01-R25, M01-M06, F01, and the principles P1-P5), `approvals.md`, `config.md` (the `policy.yaml` schema).

**Build:** one rule per file, each a pure function of an explicit `PolicyContext`. The engine composes them and returns the **strictest** verdict (ADR 0015: verdicts form an ordered lattice). Phase 1 implements the rules that need no external module: legal transition, kill switch (I10), contact limits, module availability (I6), approval-required-for-credit (I3), approval-required-for-message-kind from `policy.yaml` (I13). Rules needing evidence or a model arrive in later phases; each unimplemented rule has a skipped test naming its phase.

**Tests first:** one test per implemented rule id, named for it (`test_R03_module_availability`), plus `test_strictest_wins.py` — when two rules disagree, the stricter verdict is returned, over every pair in the lattice.

**Acceptance:** `pytest tests/unit/policy -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I3, I6, I10, I13).

**Watch:** P5 — no rule may return a lower verdict because of anything a model produced. In Phase 1 there is no model, so the engine's signature is what enforces it: a rule takes `PolicyContext`, and model-derived values enter it only as labelled, clearly-named fields. Get the signature right now and the rule is structural rather than remembered.

---

## P1-07 — The caution rule

**Files:** `src/dispute_agent/kernel/policy/signals.py`, one rule file.

**Implements:** I7, I15, ADR 0015, `agent-design.md` section 7 (what a caution signal may do).

**Build:** `CautionSignal`s may only escalate a verdict — Allow to RequireApproval to Escalate, never the reverse. The `CautionSignal` model from P0-04 already makes a lowering signal unrepresentable; this card makes the engine honour it and adds the test that it cannot be bypassed by ordering.

**Tests first:** `tests/unit/policy/test_caution_only_raises.py` — property test over random signal sets and starting verdicts: the result is never lower than the verdict without the signals.

**Acceptance:** `pytest tests/unit/policy/test_caution_only_raises.py -q`.

**Size:** S. **Model:** Sonnet, effort medium. **Opus review:** yes (I7, I15).

---

## P1-08 — `ActionExecutor` skeleton

**Files:** `src/dispute_agent/kernel/executor/executor.py`.

**Implements:** `executor.md` sections 1-6, ADR 0006 (outbox), ADR 0007 (idempotency), ADR 0010 (read-back verification), I2, I3, I4, I10.

**Build:** the outbox cycle — `plan` (re-evaluate policy at stage EXECUTION, write the PLANNED row with its idempotency key, commit) then `execute` then `verify` (independent read-back). Statuses PLANNED, SENT, VERIFIED, FAILED, UNKNOWN. The kill switch is checked **inside** `plan` and `execute`, not only at the entry point (I10). An UNKNOWN outcome becomes `NEEDS_HUMAN_REVIEW` and is never treated as success (I4). `plan` takes no "already approved" parameter — a credit needs a bound, APPROVED, unused approval found by the executor itself (I3). Test against an in-memory fake adapter.

**Tests first:**
- `test_executor_outbox.py` — the PLANNED row with its key is committed before any external call; a crash between plan and execute leaves a recoverable PLANNED row.
- `test_idempotency.py` — the same action replayed returns the original result, no second call.
- `test_kill_switch_inside.py` — with the switch on, calling `execute` directly (bypassing the entry point) still refuses.
- `test_unknown_never_success.py` — a fake whose read-back differs yields UNKNOWN, and the case cannot reach a resolved state.

**Acceptance:** `pytest tests/unit/executor -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I2, I3, I4, I10).

---

## P1-09 — Orchestrator skeleton and the cross-case hooks

**Files:** `src/dispute_agent/kernel/orchestrator/orchestrator.py`.

**Implements:** `orchestrator.md` sections 3 and 5, `FLOW.md` section 3, D3, I6.

**Build:** the step loop of `orchestrator.md` section 3 — one committed transition per step (ADR 0004), with the step limit of 12 (`orchestrator.md` section 108) and `step_limit` as its review code. The three cross-case hooks are present and wired to the Null adapters from P0-06. `ModuleUnavailable` is converted to `NEEDS_HUMAN_REVIEW` with `module_unavailable:<name>` in **one** place (I6). Phase 1 runs with fake channels and no AI: the `rules_only` reasoner is not built until Phase 2 has a pack, so the investigate step is stubbed to escalate with a reason naming the phase.

**Tests first:** `tests/e2e/test_skeleton_run.py` — a synthetic dispute runs from `RECEIVED` to a terminal state with fake ports; every state change has an event; the step limit is enforced.

**Acceptance:** `pytest tests/e2e/test_skeleton_run.py -q`.

**Size:** L. **Model:** Sonnet, effort medium.

---

## P1-10 — Timers and restart recovery

**Files:** `src/dispute_agent/kernel/orchestrator/timers.py`, the runner in `app.py`.

**Implements:** `transitions.yaml` (`timer_kinds`), `orchestrator.md` section 6, D41.

**Build:** timers are rows; nothing sleeps in memory. Entering a state arms them through transition effects, leaving cancels them. The runner is one loop that calls `tick()`; tests call `tick()` directly with a `FakeClock`. `EVIDENCE_RETRY` carries `gather_round` so the D41 limit of three is counted per round and a later round does not start at the limit.

**Tests first:**
- `test_restart_resumes.py` — arm a timer, drop the process, start again, `tick()` past the due time: the case resumes. This is the card's reason to exist.
- `test_evidence_retry_per_round.py` — three retries in round 1, then a new round gets its own three (D41).

**Acceptance:** `pytest tests/unit/orchestrator/test_timers.py tests/integration/test_restart_resumes.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P1-11 — API routes and structured logs

**Files:** `src/dispute_agent/api/{events,cases}.py`.

**Implements:** `CLAUDE.md` section 11, `observability.md`.

**Build:** `POST /events` (inbound events, returns the case) and `GET /cases/{id}` (state and events). Every log line carries `case_id` and `correlation_id`; the redaction processor from P0-09 is already in the chain.

**Tests first:** `tests/unit/api/test_routes.py` — posting the same event twice yields one case; `GET` returns the event list in order with each event's cause.

**Acceptance:** `pytest tests/unit/api -q`.

**Size:** S. **Model:** Sonnet, effort low.

---

## P1-12 — Property-based state machine tests

**Files:** `tests/unit/statemachine/test_properties.py`.

**Implements:** `CLAUDE.md` section 8 (property-based tests), `test-charter.md` (TC-SM-P1, TC-SM-P2), I4, I8.

**Build:** hypothesis over random event sequences. The properties: **no illegal state is ever reachable**; **terminal states stay terminal** except through the documented reopen paths (T41, T44-T47); **no case with an UNKNOWN action is ever in a RESOLVED state** (TC-SM-P2, I4); every state change has exactly one event.

**Tests first:** the tests are the deliverable.

**Acceptance:** `pytest tests/unit/statemachine/test_properties.py -q` with at least 200 examples per property.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P1-13 — Resolve the T90-from-GATHERING_EVIDENCE open item

**Files:** `docs/spec/transitions.yaml` or a new ADR; `docs/spec/eval-scenarios.yaml` if a scenario results.

**Implements:** the open item carried from Stage A4 (D44, `a4-review-findings.md`).

**Build:** the Stage A4 review found that `T90` from `GATHERING_EVIDENCE` is declared but no scenario reaches it: every evidence failure the specs describe ends as a FAILED evidence item that a later step acts on (`orchestrator.md` section 5). Now that `gather_evidence` is implemented, decide which is true — **either** a real cause exists (write it down and add a scenario) **or** the branch is unreachable (remove it from `transitions.yaml`). Either outcome changes a frozen spec, so it needs an ADR and the owner's approval.

**Tests first:** if a cause exists, a scenario and its test. If not, `scripts/check_scenarios.py` stops listing the edge as unwalked.

**Acceptance:** `python scripts/check_scenarios.py .` and the edge is either walked or gone.

**Size:** S. **Model:** Sonnet, effort medium. **Stop and ask:** this card changes `docs/spec/` after the `arch-v1` freeze. Raise the ADR and wait for the owner (`escalation-guide.md`).

---

## P1-14 — ADRs

**Files:** `docs/adr/` — state-machine design, idempotency and outbox, optimistic concurrency.

**Implements:** `CLAUDE.md` section 12.

**Build:** ADRs 0004, 0005, 0007 and 0008 already exist from Stage A1 and are accepted. Do **not** rewrite them. This card records what implementation taught: an "implementation notes" section appended to each, or a new ADR if a decision actually changed. A changed decision needs the owner's approval.

**Tests first:** not applicable.

**Acceptance:** `docs/PROGRESS.md` updated; no accepted ADR silently altered.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green; branch coverage of `kernel/` at or above 90%; the property tests pass; a case survives a restart; `/phase-verify 1`; **run the `safety-reviewer` subagent** (`CLAUDE.md` section 17); then `/session-handoff`.
