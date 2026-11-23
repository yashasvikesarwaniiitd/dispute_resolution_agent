# 0057 — AI calls are traced to Langfuse

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "I want to observe the AI layer"; route chosen: Langfuse). Builds on ADR 0017 (the spending guard) and observability.md section 3 (the span allow-list).

## The problem

Every AI call was already logged to `model_calls` with its purpose, model, tokens, latency, cost and outcome, but nothing showed those calls as they happened. The OpenTelemetry tracer existed with an attribute allow-list, but the app only ever wired the no-op tracer, and the spending guard that opens the `model.call` span was never given a tracer at all.

## Decision

1. **A `langfuse` tracing adapter.** It is the existing `OtelTracer`, exporting over OTLP/HTTP to Langfuse (`<host>/api/public/otel/v1/traces`, Basic auth on `public:secret`, `x-langfuse-ingestion-version: 4`), batched in the background. It needs one new dependency, `opentelemetry-exporter-otlp-proto-http`.
2. **AI calls only.** The guarded gateways (reply reader, image check, pattern analyst) get the tracer; the case steps do not. On the running app most steps are rules-only, and a trace per step would bury the AI calls. Each AI call is its own trace, and its case id is the Langfuse **session**, so one case's calls group together.
3. **Langfuse fields are derived, never added.** From the allow-listed attributes only: `langfuse.observation.type = generation`, the model name, `usage_details` (input and output tokens), `cost_details` (USD, from the guard's own price table) and `langfuse.session.id`. `case_id` and `cost_usd` join the `model.call` allow-list. Prompt and reply text are still never sent (I14), so Langfuse shows timing, tokens, cost and outcome, not content.
4. **The span lasts as long as the call.** The guard opens the span after the call returns, so its start is set `latency_ms` earlier; otherwise every call would show a latency of zero.
5. **Missing keys mean no tracing, not no app.** Without `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, the adapter wires the no-op tracer and logs `langfuse_keys_missing`. Observability is the one module whose absence changes nothing the system decides.
6. **Where it is on.** `modules.hosted.yaml` uses `langfuse`; `modules.yaml` (CI) stays `noop`. `make hosted-secrets` adds the three `LANGFUSE_*` names when `.env` holds a key, and names any that are missing.

## Why

Langfuse reads OpenTelemetry, so it plugs into the tracer port the project already had: no Langfuse SDK in the code, and another backend later is an exporter change. The product's own numbers (escalation rate, unapproved credits, time to resolution) stay computed from the audit trail; Langfuse is for watching the AI layer.

## Options considered

- **The Langfuse Python SDK.** Richer (prompt management, scores), but it is a second tracing path beside the port, and its default is to capture content. Rejected.
- **An "AI calls" screen in the console, from `model_calls`.** No account and no dependency. Offered; the owner chose Langfuse.
- **Trace every case step too.** Rejected for now (decision 2); it is a one-line change in `_build_runtime` if wanted.

## Consequences

- The owner creates a Langfuse Cloud project and puts its keys in `.env`, then `make hosted-secrets`, Secrets, Reboot.
- Scripts (`eval`, `demo`, `red_team`) still build their gateways without a tracer, so their calls are only in `model_calls`.
- The Case Explainer (ADR 0039) runs on the Agent SDK, outside the guard, and is not traced.
