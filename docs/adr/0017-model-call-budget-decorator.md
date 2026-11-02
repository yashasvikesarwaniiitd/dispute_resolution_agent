# ADR 0017: Model-call budgets and failover live in one kernel decorator around ModelGateway

Status: accepted (owner, 2026-11-02, D34)
Date: 2026-11-02

## Decision
`BudgetedModelGateway`, a kernel class implementing `ModelGateway`, wraps the primary and secondary gateways. Before each call it applies the model-call rules (calls per case, spend caps per route, the Azure credit window, Haiku-only secondary); on failure it applies failover rule F01 (only unavailable, rate-limited after retries, quota exceeded, or credit window ended). Spend comes from a `ModelUsageLedger` port over `model_calls`. The app hands only this decorator to reasoning adapters.

## Why
Budgets and failover are safety rules (I14, D17), so they belong in the kernel where they are pure and testable, not inside each provider adapter where they would be duplicated and could drift. A decorator keeps the adapters simple (one provider each) and makes "every model call goes through one guarded path" structurally true.

## Options considered
- Budgets inside each adapter: duplicated logic; a new adapter could forget them.
- A separate failover module (the ARCHITECTURE.md `failover` adapter): it would need to import two other adapters, which breaks "modules never import each other".

## Consequences
Easier: one place to test budgets and failover (TC-M*, TC-F01-*). Harder: adapters must map provider errors to `TransientPortError(reason=...)` precisely (Stage A3). Watch: ARCHITECTURE.md's `model_gateway/failover/` directory is superseded by this decorator.

## Sources
Project documents (CLAUDE.md I14; docs/HANDOFF.md D15, D17; docs/ARCHITECTURE.md section 12).
