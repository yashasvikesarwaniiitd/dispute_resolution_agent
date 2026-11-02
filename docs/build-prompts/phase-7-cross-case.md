# Phase 7: Cross-case intelligence (connect the empty box)

## Role
Principal engineer proving the architecture by adding a whole capability without touching the kernel.

## Context
Phases 0 to 6 are complete. The socket already exists: `PatternContextProvider`, `CaseOutcomeObserver`, `CautionSignal`, the empty `pattern_context` slot, the caution policy rule, and Null adapters. Read `docs/FLOW.md` section 6 carefully.


**Task cards: `docs/spec/task-cards/phase-7.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
A scheduled analyzer finds patterns across disputes and opens linked Jira investigations through the normal policy and executor path. New cases receive matching patterns as caution-only context, and recurrence after a confirmed fix is measured.

## Scope
### In scope
`modules/cross_case` adapters, a pattern analyzer job, `pattern_records` tables, the `pattern_analyst` prompt activation, two-pass reasoning, linked investigations, recurrence monitoring, two cross-case scenarios.
### Out of scope
Any change to `kernel/`. Automatic refunds, penalties or accusations based on a pattern.

## Tasks
1. Migration adding `pattern_records` (status UNCONFIRMED, CONFIRMED, DISMISSED, FIXED_MONITORING; hypothesis; evidence refs; thresholds used) and `pattern_case_links`.
2. `PatternRepository` and `SqlPatternContextProvider` implementing `PatternContextProvider`. Run the existing contract suite for the port against both the Null and Sql adapters.
3. `PatternAnalyzer`: a scheduled job that reads cases read-only and finds clusters with deterministic queries (seller, SKU, hub, scanner, time window; thresholds from `policy.yaml`, default at least 3 related disputes in 14 days).
4. Activate `prompts/pattern_analyst/v1` (set status to active after evals): competing hypotheses and the evidence that would confirm or refute each. Hypotheses only; never actions. Call it through `ModelGateway` like every other prompt.
5. Open or update a parent Jira investigation linked to the child cases by proposing `upsert_issue` through the policy engine and `ActionExecutor`. The analyzer never writes directly.
6. Two-pass reasoning in the reasoning module: pass 1 without patterns, pass 2 with them; the proposal records `pattern_influence`. Implement by extending the reasoning adapter, not the kernel.
7. `CaseOutcomeObserver` adapter that feeds closed cases to the analyzer's inputs.
8. Confirmation rule: a pattern moves to CONFIRMED only on independent evidence, such as an operations finding on the Jira issue. Outcomes decided with pattern help never count towards confirming it.
9. Recurrence monitor: disputes per cause before and after a confirmed fix, reported in the eval report.
10. Scenarios: a recurring cross-case anomaly, and a pattern that looks suspicious but is found legitimate (the system must not have credited or penalised anything because of it).
11. ADRs: pattern thresholds, anchoring control, confirmation rule.
12. Flip `cross_case` to enabled with the `sql_context` adapter in `config/modules.yaml`.

## Technical requirements
- `git diff <phase-6-tag>..HEAD -- src/dispute_agent/kernel` is empty.
- With `cross_case` disabled again, every earlier test and gate still passes.
- A pattern influence can only increase caution, proven by a property test over random pattern inputs.

## Acceptance criteria
- Enabling the module changes zero lines in `kernel/` (diff check in CI).
- The contract suite passes for both Null and Sql adapters.
- A new dispute matching an open pattern is briefed with the pattern as labelled hypothesis context, and its proposal states whether the pattern changed it (scenario test).
- No pattern, however strong, produces Allow, a credit, or an upheld charge on its own (property test and scenario).
- The analyzer creates one linked Jira parent issue, verified by read-back, and re-running it does not create a second (test).
- The recurrence report shows before and after counts for a confirmed fix on synthetic data.
- The legitimate-pattern scenario ends with no credits and no penalties.

## Deliverables
Module code, migration, activated prompt with changelog and eval results, scenarios, ADRs, updated docs and diagrams.

## Constraints
I5, I7, I2, I9. Patterns are hypotheses, never facts, until independently confirmed.

## Process
Plan mode first. If any task seems to require a kernel change, stop, explain which socket is insufficient, and propose an ADR before touching `kernel/`.

## Model and effort
Plan in Opus at effort high (the socket must hold); build on Sonnet. The `safety-reviewer` subagent confirms the kernel diff is empty and patterns cannot loosen any rule.

## Stop and ask if
The only way to meet an acceptance criterion is to edit the kernel.
