# Configuration (Stage A3)

Status: draft for owner approval. Every setting the system reads, with its type, default and who uses it. All values are illustrative synthetic configuration (D10). Secrets live only in `.env` (I12); every other file here is committed.

Plain-language summary: five small files decide how the system behaves, and a handful of environment variables hold the secrets. The app checks all of them at start-up and refuses to run with an unsafe or inconsistent combination, so a typo can never quietly switch a safety rule off.

## 1. Files

| File | Holds | Loaded by | Changes need |
|------|-------|-----------|--------------|
| `config/modules.yaml` | which modules are on, which adapter each uses, adapter settings | `infra/settings.py` -> registry | restart |
| `config/policy.yaml` | limits, timers, review kinds, credit reasons, LLM budgets | `infra/settings.py` -> `PolicyConfig` | restart; every change is recorded in `rules_version` |
| `config/runtime.yaml` | orchestrator, executor, runner, storage and HTTP settings | `infra/settings.py` | restart |
| `config/pricing.yaml` | model prices per route | `ModelUsageLedger` | restart |
| `config/release_gates.yaml` | Phase 6 release thresholds | `modules/evals` | none (read by `make eval`) |

All YAML is parsed with `yaml.safe_load` into pydantic models with `extra="forbid"`: an unknown key is a start-up error, not a silent no-op. Durations are integers with the unit in the key name (`_hours`, `_s`). Money in configuration is a string (`"10.00"`) parsed into `Decimal`.

## 2. `config/modules.yaml`

```yaml
modules:
  evidence:      {enabled: true,  adapter: synthetic_shipments, settings: {seed: 42}}
  model_gateway: {enabled: false, adapter: fake, secondary_adapter: null, settings: {drift_cache_s: 60, in_adapter_retries: 2}}
  reasoning:     {enabled: true,  adapter: rules_only}
  input_screen:  {enabled: true,  adapter: rules_based}
  tracing:       {enabled: true,  adapter: noop}
  email:
    enabled: true
    adapter: outlook_graph
    settings:
      poll_interval_s: 60
      max_concurrency: 2                          # Graph allows 4 concurrent requests per mailbox
      extended_property_guid: "<fixed GUID, generated once and committed>"
      trusted_authserv_id: null                    # optional; set from a test message (integrations.md 1.5); Outlook.com format is undocumented
  ticketing:
    enabled: true
    adapter: jira_cloud
    settings:
      poll_interval_s: 120
      issue_type: Task
      done_statuses: [Done]
      duplicate_resolutions: [Duplicate]
      field_finding_kind: "customfield_XXXXX"     # ids from GET /rest/api/3/field (integrations.md 2.4)
      field_length_cm: "customfield_XXXXX"
      field_width_cm: "customfield_XXXXX"
      field_height_cm: "customfield_XXXXX"
      field_dead_weight_kg: "customfield_XXXXX"
  ledger:        {enabled: false, adapter: simulated, settings: {failure_mode: null}}   # path: runtime.storage.ledger_db_path
  approvals:     {enabled: true,  adapter: store_backed}
  cross_case:    {enabled: false, adapter: "null"}
  evals:         {enabled: true,  adapter: default}
```

| Module | Adapters | Depends on | Notes |
|--------|----------|------------|-------|
| `evidence` | `synthetic_shipments` | — | `seed` makes synthetic data reproducible |
| `model_gateway` | `fake`, `anthropic_direct`, `foundry_agent_service`, `foundry_openai` | — | `adapter` is the primary route, `secondary_adapter` the failover route (`anthropic_direct` or null). Both are wrapped by `BudgetedModelGateway` (ADR 0017). `secondary_adapter` must differ from `adapter` |
| `reasoning` | `rules_only`, `llm` | `model_gateway` when `llm` | |
| `input_screen` | `rules_based`, `foundry_guardrails` | `model_gateway` with a Foundry primary when `foundry_guardrails` | |
| `tracing` | `noop`, `otel_foundry` | — | `otel_foundry` is `preview: true` (never required by `make verify`) |
| `email` | `fake`, `outlook_graph`, `gmail_api` | — | settings in `integrations.md` section 1; `gmail_api` added by ADR 0038 (owner-approved spec change) |
| `ticketing` | `fake`, `jira_cloud` | — | settings in `integrations.md` section 2 |
| `ledger` | `simulated` | `approvals` | |
| `approvals` | `store_backed` | — | |
| `cross_case` | `null`, `sql_context` | `ticketing` when `sql_context` | disabled -> `null` adapter, never `Disabled` |
| `image_check` | `foundry_vision`, `recorded` | — | ADR 0049 (owner-approved spec change): describes a parcel photo after a person returns a photo case; `foundry_vision` builds its own guarded `foundry_openai` gateway, `recorded` replays saved answers offline. Off: no image check runs |
| `evals` | `default` | — | |

Start-up validation (refuses to start, naming the modules): unknown module or adapter; a missing dependency (for example `ledger` on without `approvals`); `reasoning: llm` with `model_gateway` off; `secondary_adapter` not `anthropic_direct` or equal to `adapter`; an adapter marked `preview: true` while `APP_ENV=ci`; a real adapter (`outlook_graph`, `jira_cloud`, `foundry_*`, `anthropic_direct`) whose environment variables are missing. (`outlook_graph` with no cached refresh token starts, but reports `reauth_required` and escalates sends until `make email-login`.) A disabled module is wired to its `Disabled` adapter (I6), except `cross_case` (Null).

## 3. `config/policy.yaml`

The kernel-facing sections are the `PolicyConfig` model (`kernel-interfaces.md` sections 7 and 13). Full file with defaults:

```yaml
timers:
  reminder_after_hours: 48
  decision_after_hours: 72
  reopen_window_days: 7
  ops_timeout_hours: 120
  evidence_retry_minutes: 15                       # D41
  evidence_max_retries: 3
contact:
  max_messages_per_case: 3
  max_messages_per_day: 1
  day_boundary_tz: Asia/Kolkata
  max_clarifications_per_case: 1
messages:
  review_required_kinds: [evidence_request, reminder, clarification_request, handed_to_human_notice,
                          upheld_explanation, credit_confirmation]
  auto_send_kinds: []
  allowed_recipients_env: TEST_SELLER_EMAIL        # comma-separated; the loader resolves it into PolicyConfig.messages.allowed_recipients (rule R06)
credits:
  require_approval: true                           # Literal[True]: any other value fails validation
  reason_codes: [calculation_error, remeasurement_lower, duplicate_charge]
approvals:
  expire_after_hours: 72
  max_rejections_per_case: 2
  approver_ids: ["human:owner"]
cases:
  max_agent_reopens: 2
patterns:
  min_related_cases: 3
  window_days: 14
  match_min_verdict: REQUIRE_APPROVAL
baseline:
  tolerance_pct: 5                                 # rules-only baseline B7/B8: measured size within this % of
                                                   # declared counts as agreeing (agent-design.md section 5)
llm:
  max_calls_per_case: 6
  max_calls_per_case_with_patterns: 10             # two-pass reasoning doubles the investigator call per
                                                   # patterned round; start-up picks this when cross_case is on
  max_output_tokens: 1200
  call_timeout_s: 120                              # per model call (foundry.md section 7)
  pricing_file: config/pricing.yaml
llm_primary:
  spend_cap_usd: "150.00"
  credit_expires_on: null                          # set from AZURE_CREDIT_EXPIRES_ON; required when the primary is a Foundry adapter
  allow_paid_usage: false                          # from LLM_ALLOW_PAID_USAGE
llm_secondary:
  allowed_models: "claude-haiku-*"                 # glob; start-up refuses a non-matching ANTHROPIC_MODEL_HAIKU
  spend_cap_usd: "10.00"                           # from LLM_SECONDARY_SPEND_CAP_USD
telemetry:
  capture_content: false                           # true only for a deliberate test run (I14)
```

Validators (start-up errors): `review_required_kinds` and `auto_send_kinds` are disjoint and together cover every `MessageKind`; `allowed_recipients` is non-empty when `email` is on; every reason code has a pack rule that proves it; `credit_expires_on` is set when the primary route is Foundry; `capture_content` is false unless `APP_ENV=test`.

## 4. `config/runtime.yaml`

```yaml
storage:
  case_db_path: data/cases.sqlite3
  ledger_db_path: data/ledger.sqlite3             # simulated ledger, separate file (money-rules.md section 7)
orchestrator:
  max_steps_per_advance: 12
  concurrency_retries: 3
runner:
  tick_seconds: 5
  stuck_case_after_s: 60
executor:
  call_timeout_s: 10
  max_attempts: 3
  backoff_base_s: 1
  backoff_factor: 2
  backoff_cap_s: 8
  lookup_attempts: 3
  verify_schedule_s: [0, 5, 30, 120, 300]
  verify_deadline_s: 600
  execute_budget_s: 300                            # covers composite writes (executor.md section 3)
  claim_lease_s: 420                               # must exceed execute_budget_s
http:
  connect_timeout_s: 5
  read_timeout_s: 10                               # every network call has a timeout (CLAUDE.md section 7)
api:
  bind_host: 127.0.0.1                             # any other value refuses to start in v1 (ADR 0018)
  port: 8000
```

## 5. `config/pricing.yaml`

```yaml
currency: USD
effective_date: 2026-11-02
routes:
  foundry_agent_service:
    <deployment-model-id>: {input_per_mtok: "x.xx", cached_input_per_mtok: "x.xx", output_per_mtok: "x.xx", source: "<official pricing URL>"}
  foundry_openai:      {}                         # same model as above; may reference it
  anthropic_direct:
    <haiku-model-id>:  {input_per_mtok: "x.xx", output_per_mtok: "x.xx", source: "<official pricing URL>"}
```

The owner copies prices from the official pages listed in `foundry.md` section 10 when provisioning. Start-up refuses a route whose model has no price row (spend caps cannot work without one). Reasoning tokens are billed as output and are counted from the reported usage, never estimated after the fact.

## 6. `config/release_gates.yaml` (schema now; values drafted in A4 in `docs/spec/release-gates.yaml`, which supersedes the placeholder keys below; approved in Phase 6, O6)

```yaml
gates:
  unauthorised_financial_actions: {max: 0}        # fixed now: hard gate
  duplicate_side_effects: {max: 0}                # fixed now: hard gate
  ledger_verification_failures_unhandled: {max: 0} # fixed now: every failure must reach review
  correct_action_rate: {min: null}                # A4 drafts; null blocks the release until set
  escalation_rate: {max: null}
  evidence_completeness: {min: null}
  cost_per_resolved_case_usd: {max: null}
  p95_time_to_resolution_hours: {max: null}
```

`make eval` fails if any gate is null at release time, so a forgotten threshold can never pass silently.

## 7. Environment variables (`.env`, never committed)

| Variable | Secret | Required when | Used by |
|----------|--------|---------------|---------|
| `APP_ENV` | no | always (`local`, `test`, `ci`) | settings |
| `KILL_SWITCH` | no | always (default false) | `KillSwitch` |
| `FOUNDRY_PROJECT_ENDPOINT` | no | a Foundry adapter is on | Foundry adapters |
| `FOUNDRY_MODEL_DEPLOYMENT` | no | a Foundry adapter is on | Foundry adapters |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | yes | `tracing: otel_foundry` | tracing adapter |
| `ANTHROPIC_API_KEY` | yes | `anthropic_direct` is a route | that adapter's settings loader only (I14) |
| `ANTHROPIC_MODEL_HAIKU` | no | as above | must match `llm_secondary.allowed_models` |
| `LLM_SECONDARY_SPEND_CAP_USD` | no | optional | policy |
| `LLM_PRIMARY_SPEND_CAP_USD` | no | optional | policy |
| `AZURE_CREDIT_EXPIRES_ON` | no | Foundry primary | policy (M04) |
| `LLM_ALLOW_PAID_USAGE` | no | optional (default false) | policy (M04) |
| `GRAPH_CLIENT_ID` | no | `outlook_graph` | email adapter (public client, no secret; D36) |
| `GRAPH_AUTHORITY` | no | `outlook_graph` (default `https://login.microsoftonline.com/consumers/`) | email adapter |
| `GRAPH_TOKEN_CACHE_PATH` | no (the **file** is a secret: encrypted, git-ignored) | `outlook_graph` (default `data/msal_cache.bin`) | email adapter |
| `GRAPH_MAILBOX` | no | `outlook_graph` | the one mailbox the app sends from and reads |
| `GMAIL_CLIENT_ID` | no | `gmail_api` | Google OAuth desktop client (ADR 0038) |
| `GMAIL_CLIENT_SECRET` | treated as one (Google says a desktop client's is not confidential) | `gmail_api` | Google OAuth desktop client |
| `GMAIL_TOKEN_CACHE_PATH` | no (the **file** is a secret: encrypted, git-ignored) | `gmail_api` (default `data/gmail_token.bin`) | email adapter |
| `GMAIL_MAILBOX` | no | `gmail_api` | the one Gmail address the app sends from and reads |
| `TEST_SELLER_EMAIL` | no | `email` on | recipient allowlist (I11) |
| `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_PROJECT_KEY` | no | `jira_cloud` | ticketing adapter |
| `JIRA_API_TOKEN` | yes | `jira_cloud` | ticketing adapter |
| `REVIEW_CONSOLE_TOKEN` | yes | `approvals` on | review routes (ADR 0018, D32) |
| `TELEMETRY_CAPTURE_CONTENT` | no | optional (default false) | overrides `telemetry.capture_content` only when `APP_ENV=test` |

Environment overrides (spend caps, paid-usage switch, credit expiry) are applied **before** `rules_version` is computed, so every verdict records the effective values. Secrets are read once, by `infra/settings.py`, into `SecretStr` fields; their values never reach logs, events, spans, prompts or exceptions (`observability.md` section 2). `.claude/settings.json` denies Claude Code reading `.env`.
