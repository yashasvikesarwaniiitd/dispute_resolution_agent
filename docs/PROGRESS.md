# Progress

## Status

| Stage or phase | Status | Approved by owner | Notes |
|----------------|--------|-------------------|-------|
| A1 Kernel specification | Drafted; awaiting owner approval | | `docs/spec/` (7 files), ADRs 0004-0013; table checks C1-C14 and 27 schema checks pass; independent review findings fixed |
| A2 Safety and policy | Not started | | |
| A3 Integrations and Foundry | Not started | | |
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
| 0004 | Step-driven orchestrator, one committed transition per step | proposed |
| 0005 | State machine as data, with an event acceptance matrix | proposed |
| 0006 | Transactional inbox for inputs, outbox for outcomes | proposed |
| 0007 | Idempotency keys include the action sequence; every write port supports lookup by key | proposed |
| 0008 | Optimistic versions, BEGIN IMMEDIATE, an action claim, one worker process | proposed |
| 0009 | A free-text withdrawal needs human confirmation | proposed |
| 0010 | Read-back verification with a bounded retry window; "sent" is not "delivered" | proposed |
| 0011 | Money as integer paise and weight as integer grams in storage | proposed |
| 0012 | Deterministic follow-up actions; "resolved credited" means credited and told | proposed |
| 0013 | The kernel approvals table is the source of truth; ApprovalService only notifies | proposed |

## Session log

| Date | Stage or phase | Model plan | `/usage` note | What was done | Next |
|------|----------------|------------|---------------|---------------|------|
| 2026-11-02 | A1 | Opus (Claude Code desktop session, not the stage script) | not recorded | Kernel spec drafted: interfaces, schema, transitions + accept matrix, events, orchestrator, executor, traceability; ADRs 0004-0013; machine checks on the table and schema; independent review | Owner approves A1 (O9), then A2 |
