# Phase 6: Evaluation and hardening

## Role
Principal engineer proving the system behaves, and recording exactly what that proof covers.

## Context
The single-case flow works with real email and Jira, human approval and a simulated ledger. This phase measures it and attacks it. Cross-case is still off.


**Task cards: `docs/spec/task-cards/phase-6.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
A complete scenario suite, metrics, explicit release gates, failure drills and documentation that states honestly what the results do and do not show.

## Scope
### In scope
The remaining scenarios, metrics, gates, failure injection, concurrency and restart drills, kill-switch drill, security checklist, README and diagrams.
### Out of scope
Cross-case scenarios (Phase 7), production deployment.

## Tasks
1. Build the scenario simulator covering the list in `docs/FLOW.md` section 8, except the two cross-case scenarios. Each scenario has a human-reviewed expected outcome and expected final state. Add a message-review scenario: a review-required message waits for approval and is then sent unchanged.
2. Metrics: correct-action rate, successful resolution rate, autonomous resolution rate, escalation rate with reasons, unauthorised financial actions (must be 0), duplicate side-effect rate (must be 0), ledger verification failures, evidence completeness, time to resolution, cost per resolved case.
3. `config/release_gates.yaml` with explicit thresholds. `make eval` exits non-zero when a gate fails.
4. Failure drills: email and ticket API failures, ambiguous ledger result, late reply after timeout, silent seller timers, duplicate events, retries, concurrent events on one case.
5. Kill-switch drill: turn it on mid-flow; no external write may occur afterwards (test).
6. Restart drill: stop the process at each waiting state and resume.
7. Report generator: a markdown or HTML eval report with per-scenario results and metric tables.
8. Security checklist review: least privilege on Outlook scopes and the Jira token, log redaction, prompt injection suite, secret scanning in CI.
9. Docs: README with an architecture diagram (Mermaid), the demo walkthrough, the honest-limits section, and the module on or off guide.
10. ADR: evaluation approach and gate thresholds, with the reasoning behind each.
11. Foundry evaluation (optional, supplementary): run Foundry's agent evaluators against the hosted prompt agents on the scenario inputs. Choose the evaluators that fit tool-less agents (decide in an ADR). Local gates stay authoritative.
12. Red team: run the Foundry AI Red Teaming scan (preview) against the hosted agents with the injection scenarios. Every failure becomes a new local scenario.
13. Observability check: traces reach Application Insights with no message content; the eval report shows cost per case from `model_calls`.

## Technical requirements
- Evals are reproducible: seeded data, recorded model fixtures by default, live runs opt-in.
- Gate thresholds live in configuration, not code.

## Acceptance criteria
- `make eval` runs every in-scope scenario, writes the report, and fails when any gate is missed.
- Unauthorised financial actions and duplicate side effects are 0 across all scenarios (gate).
- The kill-switch and restart drills pass as automated tests.
- The README states that results come from synthetic data and do not prove production readiness.

## Deliverables
Scenario suite, metrics, gates, drills, report, README, ADR.

## Constraints
Do not claim safety or effectiveness beyond what the gates demonstrate.

## Process
Plan mode first. Propose the gate thresholds with reasoning and wait for approval.

## Model and effort
`opusplan`, effort medium. Use Opus (the `architect` subagent) to reason about gate thresholds; Sonnet writes scenarios and drills.

## Stop and ask if
A scenario's expected outcome is debatable. Ask the owner to decide and record it.
