# Specification index

Status: Stage A5, awaiting owner approval and the `arch-v1` tag. Every document below is approved. After the tag, **any change to `docs/spec/` needs an ADR and the owner's approval** — see `escalation-guide.md`.

Authority order when documents disagree (`CLAUDE.md` section 18): `docs/spec/` beats `docs/ARCHITECTURE.md`, which beats a phase prompt, which beats a sketch in `docs/FLOW.md`. `CLAUDE.md` invariants beat everything.

## Stage A1 — Kernel (approved, D27)

| Document | What it decides | Invariants |
|----------|-----------------|------------|
| `kernel-interfaces.md` | Every port, domain model, enum and context shape. Sections 13-15 are the Stage A2, A3 and A4 amendments. The authoritative data model. | I1, I5, I8, I9 |
| `schema.sql` | The database: tables, CHECK constraints, partial unique indexes, triggers, schema checks S1-S27. | I3, I4, I8 |
| `transitions.yaml` | States, groups, timer kinds, triggers, guards, steps, 49 transitions, the event acceptance matrix, checks C1-C16. | I8, I4 |
| `events-catalog.md` | Every event type and its payload. | I8 |
| `orchestrator.md` | The step loop, one committed transition per step, module availability and fallbacks. | I4, I6 |
| `executor.md` | Outbox, idempotency keys, read-back verification, the kill switch inside. | I2, I3, I10 |
| `traceability.md` | Every invariant mapped to its mechanisms and a test that tries to break it. | I1-I15 |

## Stage A2 — Safety, policy and money (approved, D34)

| Document | What it decides | Invariants |
|----------|-----------------|------------|
| `policy-rules.md` | Rules R01-R25, money rules M01-M06, failover F01, principles P1-P5. The verdict lattice. | I3, I5, I7, I13, I15 |
| `money-rules.md` | The three code-proven credit reasons, integer paise and grams, the fault vocabulary. | I3, I9 |
| `approvals.md` | Approval records, hash binding, what a rejection does per intent kind. | I3, I13 |
| `threat-model.md` | Threats T01-T21, each with its mitigation and test ids. | I5, I12, I14, I15 |
| `test-charter.md` | Test ids per rule, the 12-item injection corpus, `TC-PR-NEU-1..6`. | I1-I15 |

## Stage A3 — Integrations and Foundry (approved, D37)

| Document | What it decides | Invariants |
|----------|-----------------|------------|
| `integrations.md` | Outlook Graph and Jira Cloud: send, read-back, polling, ambiguity. | I2, I4 |
| `foundry.md` | Agent Service, pinned stateless agents, drift, structured output, failover. | I14 |
| `config.md` | Every configuration key with its default and its schema. | I6, I14 |
| `observability.md` | Log fields, redaction, spans, the KPI table. | I12, I14 |

## Stage A4 — Agent and evals (approved, D44)

| Document | What it decides | Invariants |
|----------|-----------------|------------|
| `agent-design.md` | The briefing and its trust labels, the action menu, proposal validation, failure handling, the rules-only baseline B1-B9, the two-pass anchoring control. | I1, I4, I5, I7, I9 |
| `prompt-review.md` | The adversarial review of all four prompts: ~29 named failure modes, the change for each, the eval cases that prove it. | I5, I9 |
| `eval-scenarios.yaml` | 59 scenarios with expected paths, actions, baselines and outcomes. Paths machine-checked. | I1-I15 |
| `release-gates.yaml` | Hard, quality and cost gates with metrics and denominators; the claims the project must not make. Thresholds are draft until Phase 6 (O6). | I3, I5, I11 |
| `synthetic-data.md` | Generator knobs, distributions, seeds, 15 named fixtures, reply and finding banks, the fault vocabulary. | I11 |
| `a4-review-findings.md` | The independent review record: 9 blocking and 22 major findings, with resolutions. | — |

## Stage A5 — Build plan (this stage)

| Document | What it decides |
|----------|-----------------|
| `task-cards/phase-{0,1,2,3,4,4b,5,6,7}.md` | 109 cards: files, spec sections, tests first, acceptance command, size, dependencies, model and effort. |
| `escalation-guide.md` | When Sonnet must stop and use `architect` or `safety-reviewer`, and what needs the owner. |
| `risk-register.md` | Risks with their trigger, mitigation and owner, plus the milestone plan. |
| `INDEX.md` | This file. |

## Tooling

| File | What it does |
|------|--------------|
| `scripts/check_scenarios.py` | Walks every scenario path against `transitions.yaml`, checks invariant coverage and the fields the gates read. Exits non-zero; wired into `make verify` from Phase 0. |

## Invariant coverage

Every invariant in `CLAUDE.md` section 3 is specified in at least one document above and exercised by at least one scenario. `traceability.md` holds the full mapping; `scripts/check_scenarios.py` proves the scenario half of it mechanically.

| Invariant | Specified in | Enforced by (phase) |
|-----------|--------------|---------------------|
| I1 AI never writes | `kernel-interfaces.md`, `agent-design.md` | P4-05 |
| I2 Everything through the executor | `executor.md` | P1-08, P3-04 |
| I3 No credit without approval | `executor.md`, `approvals.md`, `money-rules.md` | P5-04 |
| I4 Unknown becomes human review | `executor.md`, `orchestrator.md` | P1-08, P5-05 |
| I5 Outside text is data | `agent-design.md`, `threat-model.md` | P4-01, P4-02, P4-09 |
| I6 Disabled module escalates | `orchestrator.md`, `config.md` | P0-06, P0-07 |
| I7 Patterns only add caution | `agent-design.md` section 7, ADR 0025 | P4-08, P7-11 |
| I8 State changes through the machine | `transitions.yaml` | P1-03, P1-04 |
| I9 Deterministic money | `money-rules.md` | P2-02, P4-07 |
| I10 Kill switch inside the executor | `executor.md` | P1-08, P6-05 |
| I11 Synthetic data only | `synthetic-data.md` | P1-05, P2-04 |
| I12 No secrets anywhere | `observability.md`, `threat-model.md` | P0-09, P4b-12 |
| I13 Approved text is what is sent | `approvals.md` | P3-06 |
| I14 Every model call through the gateway | `foundry.md`, `config.md` | P4-03, P4-10, P4b-03 |
| I15 Screening is advisory | `policy-rules.md`, `threat-model.md` | P4-09, P4b-09 |

## Open decisions still outstanding

| # | Question | Resolved in |
|---|----------|-------------|
| O1 | `gpt-5-mini` versus `gpt-5.4-mini` | Phase 4b (P4b-01, P4b-11) |
| O2 | Guardrail integration approach | Phase 4b (P4b-09) |
| O3 | Whether to use Foundry evaluations | Phase 6 (P6-10) |
| O4 | Microsoft Agent Framework | Backlog; ADR only |
| O5 | Graph authentication least privilege | Phase 3 / Phase 6 (P6-08) |
| O6 | Release gate thresholds | Phase 6 (P6-03, P6-13) |
| O7 | When to relax message review | Owner, any time |
| O8 | Haiku spend cap and model id | Phase 4 (P4-10) |

One item is carried from the Stage A4 review: **T90 from `GATHERING_EVIDENCE`** has no scenario because nothing in the current design reaches it. Card **P1-13** resolves it — a documented cause plus a scenario, or removal from `transitions.yaml`. Either changes a frozen spec and needs an ADR.
