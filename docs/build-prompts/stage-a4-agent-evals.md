# Stage A4: Agent, prompts and evaluation design (Opus)

## Role
Principal AI engineer designing what the agent sees, what it may say, and how we know it behaves.

## Context
Stages A1 to A3 are approved. Read `prompts/README.md`, the four prompt directories (they are drafts) and `docs/spec/`.

## Objective
A precise agent design, reviewed prompts, a complete scenario catalogue with expected outcomes, draft release gates and a synthetic data specification.

## Scope
### In scope
Briefing and proposal schemas, prompt review, scenarios, gates, synthetic data.
### Out of scope
Implementation and live model runs.

## Tasks
1. `docs/spec/agent-design.md`: the `CaseBriefing` and `Proposal` schemas with size caps and truncation rules, the allowed-actions menu per state, failure handling (invalid output, budget, guardrail), the rules-only baseline as a decision table, and the two-pass reasoning design for Phase 7.
2. Adversarial review of every prompt in `prompts/`: look for ways to make the model act on untrusted text, invent numbers or skip escalation. Produce a final v1 for each, with a changelog and a list of eval cases per prompt.
3. `docs/spec/eval-scenarios.yaml`: at least the 16 scenarios in `docs/FLOW.md` section 8, each with seed data reference, expected state path, expected action and final outcome, and the invariant it exercises. Mark Phase 7 scenarios.
4. `docs/spec/release-gates.yaml` (draft): thresholds with reasoning, and a list of claims the project must not make.
5. `docs/spec/synthetic-data.md`: generator knobs (sellers, SKUs, hubs, scanners, shipments, measurements, ledger entries), distributions and seeds.

## Technical requirements
Every scenario has a human-reviewable expected outcome. Every invariant and every final outcome is exercised by at least one scenario.

## Acceptance criteria
- A coverage table shows invariants and outcomes against scenarios, with no gaps.
- Each prompt change is justified by a named failure mode.
- Gates are numeric, justified and configuration-ready.

## Deliverables
Files in `docs/spec/`, revised `prompts/`, ADRs, updated `docs/HANDOFF.md`.

## Constraints
I1, I5, I9, I15. Prompts stay provider-neutral.

## Process
Outline first. Present the coverage table to the owner for review.

## Model and effort
Opus at effort high for tasks 1 and 2; medium for the rest.

## Stop and ask if
A scenario's correct outcome is debatable. Ask the owner to decide, and record the decision.
