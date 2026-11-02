# Phase 2: Weight-dispute pack, deterministic calculation and evidence

## Role
Principal engineer encoding domain rules as configurable, testable code, with no AI involved.

## Context
The kernel works with fake ports. Read the public documentation pages listed below before coding, and link them in the ADR:
- https://help.delhivery.com/docs/weight-dispute
- https://help.delhivery.com/docs/calculating-volumetric-weight
- https://help.delhivery.com/docs/delhivery-miles-wallet
These describe a public workflow. They do not describe any internal system. Treat every rate, limit and threshold as illustrative configuration.


**Task cards: `docs/spec/task-cards/phase-2.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
For a synthetic disputed shipment the system gathers evidence from each source, records what is missing or failed, recalculates the charge deterministically, and reconciles it against a simulated ledger.

## Scope
### In scope
`DomainPack` implementation `weight_dispute`, rate card and rules as configuration, a seeded synthetic data generator, a synthetic evidence adapter, the ledger read side, reconciliation checks.
### Out of scope
Posting credits, approvals, email, Jira, LLM, cross-case.

## Tasks
1. Implement `DomainPack` for `weight_dispute`: eligibility (claims only on delivered shipments), evidence plan, `assess`, allowed actions per state.
2. Calculator: volumetric weight = L x B x H (cm) / divisor, divisor configurable (default 5000); chargeable weight rules (dead weight below the configured threshold; maximum of dead and volumetric above it; packaging-type exceptions); synthetic zones and slabs. All in configuration; `Decimal` arithmetic; documented rounding.
3. Seeded synthetic generator: sellers, SKUs, hubs, scanners, shipments, measurements, images (placeholder references), ledger entries including the four wallet transaction types, and the kurta fixture (declared 500 g, 20 x 15 x 10 cm; measured 1.1 kg, 30 x 22 x 15 cm; illustrative extra charge of INR 38).
4. `SyntheticShipmentSource` implementing `EvidenceSource`. Support injected failures: a source can return found, missing or failed.
5. Evidence service: gather from every planned source, persist `evidence_items` with status, never invent missing values.
6. Reconciliation: expected charge versus ledger charge; duplicate transactions; existing credits or reversals; flag definite calculation errors.
7. Wire evidence and calculation into the orchestrator stages (`GATHERING_EVIDENCE`, `CALCULATING`) with the Phase 1 fakes still in place for channels.
8. ADR: which public facts are used, which are assumptions, and why every figure is configuration.

## Technical requirements
- Property-based tests for the calculator: monotonic in each dimension; volumetric never negative; rounding stable.
- The pack imports `kernel` only.

## Acceptance criteria
- The kurta fixture yields volumetric weight 1.98 kg and chargeable weight 1.98 kg with the default configuration (test).
- An undelivered shipment ends in `REJECTED_INELIGIBLE` (test).
- With the shipment source set to fail, the evidence record shows `failed`, nothing is invented, and the case continues or escalates per policy (test).
- A planted duplicate ledger charge is flagged by reconciliation (test).
- Changing the divisor in configuration changes results with no code change (test).

## Deliverables
Pack code, configuration, generator, tests, ADR, updated docs.

## Constraints
I9 applies: calculations are code. I11: synthetic data only. Do not present the INR 38 charge as a real tariff anywhere.

## Process
Plan mode first. Quote the documentation passages you rely on in the plan, with links.

## Model and effort
`opusplan`, effort medium. Sonnet builds the calculator and generator from the approved plan. Escalate to Opus if a public-documentation rule is ambiguous.

## Stop and ask if
The public documentation is ambiguous or has changed in a way that affects a rule.
