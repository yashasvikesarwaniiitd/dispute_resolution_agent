# Progress

## Status

| Stage or phase | Status | Approved by owner | Notes |
|----------------|--------|-------------------|-------|
| A1 Kernel specification | Approved | Owner, 2026-11-02 (D27) | `docs/spec/` (7 files), ADRs 0004-0013; table checks C1-C16 and 27 schema checks pass; review findings fixed; product decisions D19-D26 recorded |
| A2 Safety and policy | Approved | Owner, 2026-11-02 (D34) | 5 spec files, ADRs 0015-0018; checkpoint D28-D31 approved |
| A3 Integrations and Foundry | In progress | | |
| A4 Agent and evals | Not started | | |
| A5 Build plan and freeze (`arch-v1`) | Not started | | |
| 0 Foundations and sockets | Not started | | |
| 1 Kernel | Not started | | |
| 2 Weight-dispute pack | Not started | | |
| 3 Real channels | Not started | | |
| 4 Agent reasoning | Not started | | |
| 4b Foundry integration | Not started | | |
| 5 Approvals and ledger | Not started | | |
| 6 Evaluation and hardening | Not started | | |
| 7 Cross-case intelligence | Not started | | |

## ADRs

Add one line per ADR: number, title, status. 0001-0003 are reserved for Phase 0.

| ADR | Title | Status |
|-----|-------|--------|
| 0004 | Step-driven orchestrator, one committed transition per step | accepted |
| 0005 | State machine as data, with an event acceptance matrix | accepted |
| 0006 | Transactional inbox for inputs, outbox for outcomes | accepted |
| 0007 | Idempotency keys include the action sequence; every write port supports lookup by key | accepted |
| 0008 | Optimistic versions, BEGIN IMMEDIATE, an action claim, one worker process | accepted |
| 0009 | A free-text withdrawal needs human confirmation | accepted (D20) |
| 0010 | Read-back verification with a bounded retry window; "sent" is not "delivered" | accepted |
| 0011 | Money as integer paise and weight as integer grams in storage | accepted |
| 0012 | Deterministic follow-up actions; "resolved credited" means credited and told | accepted (D21) |
| 0013 | The kernel approvals table is the source of truth; ApprovalService only notifies | accepted |
| 0014 | An unclear seller reply gets one clarification, then a person | accepted (D19) |
| 0015 | Verdicts form an ordered lattice; the strictest wins; model output only raises | accepted (D28) |
| 0016 | Credits only for code-proven reasons; no amount caps, structural limit only | accepted (D29, D30) |
| 0017 | Model-call budgets and failover live in one kernel decorator around ModelGateway | accepted (D34) |
| 0018 | Human events are authenticated; the actor comes from the session | accepted (D32) |

## Session log

| Date | Stage or phase | Model plan | `/usage` note | What was done | Next |
|------|----------------|------------|---------------|---------------|------|
| 2026-11-02 | A1 | Opus (Claude Code desktop session, not the stage script) | not recorded | Kernel spec drafted: interfaces, schema, transitions + accept matrix, events, orchestrator, executor, traceability; ADRs 0004-0013; machine checks on the table and schema; independent review | Owner approves A1 (O9), then A2 |
| 2026-11-02 | A1 | Opus | not recorded | Owner made 8 product decisions (D19-D26); clarification follow-up added (ADR 0014, X14, checks C15-C16) | Owner sign-off O9, then A2 |
| 2026-11-02 | A2 | Opus | not recorded | Checkpoint approved (D28-D31); policy rules R01-R25, M01-M06, F01; threat model T01-T21; approvals; money rules; test charter; ADRs 0015-0018; independent review | Owner approves A2, then A3 |
