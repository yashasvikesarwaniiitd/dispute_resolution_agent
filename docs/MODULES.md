# Modules: what is on, and how to switch them

Modules are switched in `config/modules.yaml`. Start-up validates the file and refuses an
unsafe combination (for example the ledger without approvals) before anything runs. A module
that is off is never skipped silently: a case that needs it goes to a person with the reason
`module_unavailable:<name>` (invariant I6).

| Module | Shipped | Adapters | Notes |
|--------|---------|----------|-------|
| evidence | on | `synthetic_shipments` | serves every synthetic fixture by shipment reference |
| reasoning | on | `rules_only`, `llm` | `llm` needs `model_gateway` on |
| model_gateway | **off** | `fake`, `foundry_agent_service`, `foundry_openai`, `anthropic_direct` (failover) | costs money; `docs/SETUP_FOUNDRY.md` |
| input_screen | on | `rules_based`, `foundry_guardrails` | advisory: a flag only adds caution (I15) |
| tracing | on | `noop`, `otel_foundry` (preview) | content is never exported (I14) |
| email | on | `fake`, `outlook_graph` | `outlook_graph` needs the owner's test mailbox |
| ticketing | on | `fake`, `jira_cloud` | `jira_cloud` needs the owner's test project |
| ledger | on | `simulated` | its own SQLite file; depends on approvals (I3) |
| approvals | on | `store_backed` | the approvals table and the review console |
| cross_case | on | `sql_context`, `null` | patterns as caution only (I7); needs ticketing; an hourly job on the runner (ADR 0036) |
| evals | on | `default` | |

## Switching

Change `enabled` or `adapter`, then restart. Examples:

- **Use the model:** `model_gateway: {enabled: true, adapter: foundry_agent_service}` and
  `reasoning: {enabled: true, adapter: llm}`. Requires the Foundry setup and approved spend.
- **Real email:** `email: {enabled: true, adapter: outlook_graph}`. Start-up refuses until the
  owner's test account is configured.
- **Turn the ledger off:** cases that would credit go to a person (`module_unavailable:ledger`).

## The kill switch

A row in the case database (`control_flags`). When it is on, no email, issue or credit leaves
the system: the executor checks it before planning and again immediately before every write,
and an unreadable switch counts as on (I10).
