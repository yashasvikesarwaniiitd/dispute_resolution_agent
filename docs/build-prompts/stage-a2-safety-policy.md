# Stage A2: Safety, policy and money rules (Opus)

## Role
Principal architect and security engineer defining every rule that decides whether the system may act.

## Context
Stage A1 is approved. Read `docs/spec/` from A1, and `CLAUDE.md` invariants.

## Objective
A complete rule catalog, threat model and money-handling specification, each rule traceable to an invariant and a test.

## Scope
### In scope
Policy rules and verdict composition, threat model, approvals, credit and ledger semantics, test charter.
### Out of scope
Implementation, prompts, Foundry details.

## Tasks
1. `docs/spec/policy-rules.md`: rule catalog with ids (R01...), inputs, verdict, reason code, config key and negative tests. Define the verdict ordering so signals can only move a verdict towards more caution (I7, I15). Cover transitions, kill switch, contact limits, module availability, credit rules (I3), message review (I13), LLM budget, guardrail block, the credit window, per-route spend caps, and the failover rules (exactly what may and may not trigger failover).
2. `docs/spec/threat-model.md`: assets, actors, trust boundaries and threats (direct and indirect prompt injection, forged or spoofed replies, wrong recipient, replay, duplicate credit, secret and log leakage, cost exhaustion, misuse of approvals, model misbehaviour). Map each threat to a mitigation, an invariant and a test. State residual risks.
3. `docs/spec/approvals.md`: approval record fields, binding to amount or message text hash, rejection and expiry behaviour, audit fields.
4. `docs/spec/money-rules.md`: credit eligibility, reason codes, amount derivation from deterministic results, duplicate detection, simulated ledger semantics, the verification checklist, rounding.
5. `docs/spec/test-charter.md`: for every rule a negative test, plus the property tests (state machine, calculator, policy composition).

## Technical requirements
No rule may depend on LLM output except as labelled input data. Every consequential write has a rule that can refuse it.

## Acceptance criteria
- Every rule maps to an invariant and has at least one negative test.
- Every threat maps to a mitigation and a test, or is listed as a residual risk.
- A written proof sketch that no sequence of verdicts and signals can produce Allow for a credit without an approval record.

## Deliverables
Files in `docs/spec/`, ADRs, updated `docs/HANDOFF.md`.

## Constraints
I1 to I15. No relaxation of an invariant without the owner's approval.

## Process
Outline first. Present the verdict ordering and the proof sketch to the owner before writing the rest.

## Model and effort
Opus at effort high. This is the most safety-critical design in the project.

## Stop and ask if
A requirement can only be met by letting model output influence a financial write.
