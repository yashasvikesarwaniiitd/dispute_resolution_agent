# Observability (Stage A3)

Status: draft for owner approval. Covers logs, redaction, traces and the metrics behind each success KPI (Phase 6 prompt, task 2). Invariants: I12 (no secrets), I14 (no prompt or response content in telemetry unless a test run asks for it).

Plain-language summary: three kinds of record exist. The **event log** is the official audit trail (A1). **Logs** are for engineers debugging. **Traces** show how long each step and external call took. None of them ever contains a secret, and logs and traces never contain what the seller or the AI wrote. Every product metric is computed from the event log and the tables, not from logs, so the numbers are as trustworthy as the audit trail.

## 1. Logs (structlog, JSON lines)

Every line has:

| Field | Always | Meaning |
|-------|--------|---------|
| `ts` | yes | ISO 8601 UTC |
| `level` | yes | debug, info, warning, error |
| `event` | yes | a stable snake_case name, for example `step_committed`, `executor_send_failed` |
| `app_env` | yes | local, test, ci |
| `correlation_id` | yes | `events-catalog.md` section 4 (`corr_none` outside any chain) |
| `case_id` | when known | |
| `event_id` | when known | the input event being handled |
| `step`, `state` | in steps | step name and the case state at the start |
| `module`, `adapter` | in adapters | |
| `action_id`, `action_type`, `idempotency_key` | in the executor | the key is not a secret |
| `duration_ms` | for timed operations | |
| `error_code`, `error_class` | on failure | the `KernelError.code`; never the raw exception message from a library |
| `http_status`, `retry_after_s`, `attempt` | for HTTP calls | |

Never logged, at any level: message subjects and bodies, reply text, AI prompts or outputs, approval notes, ledger amounts paired with seller identities, secrets, tokens, authorization headers. Instead log the `content_hash` and length.

## 2. Redaction (a structlog processor, last before rendering)

| What | Rule |
|------|------|
| Email addresses | `local@domain` -> `l***@domain` (domain kept for debugging routing) |
| Names | values of keys named `name`, `seller_name`, `from_name`, `to_name` -> `***` |
| Known secrets | any value equal to a loaded `SecretStr` -> `***SECRET***` |
| Token shapes | `Bearer <...>`, `Basic <...>`, `sk-ant-...`, JWTs (`eyJ...`), Atlassian API tokens (`ATATT...`), strings of 32+ base64url characters in a key containing `token`, `secret`, `key`, `password` -> `***REDACTED***` |
| Exceptions | `exc_info` is rendered with the same processor; library exception messages are replaced by the mapped `error_code` in `error` fields and kept only in the traceback, which is redacted line by line |
| Untrusted text | keys `body`, `text`, `subject`, `note`, `rationale`, `content` are dropped (not masked) and replaced by `<key>_hash` and `<key>_len` |

Tests TC-SEC-01 and TC-SEC-02 (`test-charter.md`) run the scenario suite with sentinel secrets and content, then scan logs, spans, the database and fixtures for them.

## 3. Traces (`Tracer` port; `otel_foundry` adapter from Phase 4b, preview)

Span names and the only attributes allowed (an allow-list; anything else is dropped by the adapter):

| Span | Attributes |
|------|------------|
| `dispatch` | `case_id`, `event_type`, `reaction`, `correlation_id` |
| `step.<name>` | `case_id`, `state_from`, `state_to`, `trigger`, `transition_id` |
| `policy.evaluate` | `stage`, `intent_type`, `verdict`, `rule_ids_fired` (comma list) |
| `executor.execute` / `executor.verify` | `action_type`, `action_id`, `outcome`, `attempts`, `found_by_lookup` |
| `http.<system>.<operation>` | `http.status_code`, `retry_after_s`, `attempt`, `server.address` |
| `model.call` | `purpose`, `route`, `deployment`, `model_id`, `prompt_name`, `prompt_version`, `prompt_hash`, `input_tokens`, `output_tokens`, `latency_ms`, `outcome` (`ok`, `invalid_output`, `blocked`, `budget`, `error`), `failover_from` |

Content (prompt and response text) is never a span attribute or event unless `telemetry.capture_content` is true, which is allowed only with `APP_ENV=test` (I14). The Foundry SDK's own content recording is switched off explicitly by the adapter, and automatic instrumentation is disabled (`foundry.md` section 9).

## 4. Metrics and KPIs

Computed by `modules/evals/metrics` from the case database (events, actions, approvals, model_calls) after a scenario run, or over a time window in the pilot. Definitions are exact so the release gates can be checked.

| KPI (Phase 6) | Definition | Source |
|---------------|------------|--------|
| Correct-action rate | scenarios whose proposal action equals the scenario's expected action / scenarios with an expected action | `proposals` joined to the scenario catalogue (A4) |
| Successful resolution rate | cases reaching RESOLVED_CREDITED or RESOLVED_UPHELD where the scenario expects that outcome / cases that expect a resolution | `cases.state`, catalogue |
| Autonomous resolution rate | resolved cases that never entered NEEDS_HUMAN_REVIEW / resolved cases (approvals by the reviewer do not count as human takeover) | `events` STATE_CHANGED |
| Escalation rate (with reasons) | cases that entered NEEDS_HUMAN_REVIEW / all cases, broken down by `review_code` (and `detail` for `policy_denied`) | `events` STATE_CHANGED payload |
| Unauthorised financial actions (must be 0) | POST_CREDIT actions without a bound human approval, plus ledger CREDIT entries with no matching VERIFIED action | `actions`, `approvals`, ledger |
| Duplicate side-effect rate (must be 0) | external writes whose idempotency key appears twice in the external system (fake/simulated stores count them), plus two CREDIT entries for one (shipment, reason) | adapter stores, ledger |
| Ledger verification failures | POST_CREDIT actions that reached UNKNOWN; each must also have reached NEEDS_HUMAN_REVIEW (gate: unhandled = 0) | `actions`, `events` |
| Evidence completeness | evidence items FOUND / evidence items required by the plan, per gather round | `evidence_items` |
| Time to resolution | `closed_at - opened_at` for resolved cases: median and p95, in hours (fake clock in scenarios) | `cases` |
| Cost per resolved case | sum of `model_calls.cost_usd_micros` / resolved cases, per route | `model_calls` |
| Review load (supporting) | approvals requested per case; median decision time; rejection rate | `approvals` |
| Failover rate (supporting) | `MODEL_ROUTE_FAILOVER` events / model calls, by reason | `events`, `model_calls` |

Operational counters (logs only, not KPIs): inbox retries, concurrency conflicts, stuck-case sweeps, deferred redeliveries, verification retries.

## 5. Health

`GET /healthz` (from Phase 0) lists each module, enabled or disabled, its adapter, and for real adapters the last successful call time and the last error code (no messages, no secrets). The kill switch state and the policy `rules_version` are included.
