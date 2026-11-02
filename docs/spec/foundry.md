# Microsoft Foundry and the secondary model (Stage A3)

Status: draft for owner approval. Every external claim cites official documentation read on 2026-11-02. Owner decisions in force: D12 (tool-less prompt agents, stateless, Foundry never owns case state), D15 (`gpt-5-mini`, Global Standard, South India), D17 (Haiku secondary for availability only), D18 (credit window).

Plain-language summary: the AI runs as small single-purpose "agents" in Microsoft Foundry, one per prompt, with no tools and no memory. Our code sends one request, gets one structured answer, checks it, and throws the conversation away. The definitions of those agents are generated from our repository and checked before calls (at most a minute old), so nobody can quietly change how the AI behaves. If Foundry is down, the same prompt goes to Claude Haiku through Anthropic's API. Spend is counted on every call against caps you set.

## 1. Model, region and lifecycle

| Fact | Value | Source |
|------|-------|--------|
| `gpt-5-mini` (2025-08-07) Global Standard in South India | available | [region availability](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability) |
| `gpt-5.4-mini` (2026-03-17) Global Standard in South India | available (O1 comparison candidate) | same |
| `gpt-5-mini` retirement | 2027-07-13 (GA) | [retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule) |
| `gpt-5.4-mini` retirement | 2027-09-21 | same |
| `gpt-4.1-mini` | **Deprecated**; base inference retires 2027-04-14, fine-tuned deployments 2027-10-14 (FOUNDRY_SETUP.md said 2026-10-14; corrected). Deprecated models cannot be newly deployed on new subscriptions, so it stays on the avoid list | same, and [retirements](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements) |
| Retired model behaviour | `410 Gone` | [retirements](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirements) |
| Agent Service in South India | Responses API: yes; Agents: yes | [limits, quotas, regions](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions) |
| gpt-5 family registration | "If you're using gpt-5 models, registration is required"; whether this covers `gpt-5-mini` is not stated | same |

Retirement watch: the project will be finished long before July 2027; `make foundry-check` prints the deployment's model version so a retirement notice is visible.

## 2. Authentication and roles

- Microsoft Entra ID only (I14). `DefaultAzureCredential` (`az login` locally). The SDK supports Entra auth only ([azure-ai-projects readme](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-projects-readme?view=azure-python)).
- Project endpoint: `https://<resource>.services.ai.azure.com/api/projects/<project>` (`FOUNDRY_PROJECT_ENDPOINT`).
- Roles, from [Foundry RBAC](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry). The roles were renamed: Azure AI User became Foundry User, and Azure AI Project Manager became Foundry Project Manager.
  - **Foundry User**: build, including creating agents. Needed for `make agents-sync` (the owner).
  - **Foundry Agent Consumer**: call agents only, scopable to the project or a single agent. This is the least-privilege role for the runtime identity. In the pilot the runtime runs as the owner's `az login` identity; a separate identity holding only Agent Consumer is the recommended hardening, recorded in the checklist as optional.

## 3. Prompt agents

One agent per runtime prompt, named `wd-<prompt_name>` (for example `wd-case_investigator`). Each prompt version is an agent **version**; there are at most 1,000 versions per agent ([limits](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions)).

- **SDK:** `azure-ai-projects >= 2.3.0`. 2.x is not compatible with 1.x ([prompt agent quickstart](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent)).
- **Definition** (`project.agents.create_version(agent_name=..., definition=PromptAgentDefinition(...))`):
  - `model` = `FOUNDRY_MODEL_DEPLOYMENT`.
  - `instructions` = the rendered `system.md` (static: per-case data goes in the user input, never in instructions).
  - `text.format = {type: "json_schema", name, schema: <provider schema>, strict: true}`. The REST reference documents `text.format` with `json_schema` on prompt agent definitions ([Foundry REST](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/aiproject)).
  - **No tools** (D12, I1).
- **Invocation** (stateless, one call per request): `openai_client = project.get_openai_client()`, then `responses.create(input=<rendered user message>, max_output_tokens=..., store=False, extra_body={"agent_reference": {"name": "wd-<prompt>", "version": "<pinned>", "type": "agent_reference"}})`.
  - `agent_reference` with an optional `version` is documented in the REST reference. The version is **always pinned** from the lock file, because the behaviour of an unpinned call is not documented.
  - `store=False` is documented with agents: "the service doesn't persist the response" ([runtime components](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components)). It "isn't a blanket guarantee of zero retention" ([responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses)), which is acceptable because all data is synthetic (I11).
  - No conversation is created; the quickstart marks conversations optional ([quickstart](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent)).

**Documentation conflict (reported, design does not depend on it):** the structured-outputs page says "structured outputs aren't supported with … Foundry Agents Service" ([structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)), while the REST reference documents `text.format` json_schema on prompt agents. The design therefore:
1. always validates output in our code (section 6), whatever the provider does;
2. tries `text.format` at `make agents-sync`; if the service rejects it, the agent is created without it, and the lock file records `structured_output: false`;
3. if invalid-output rates in Phase 4b evals are high without provider enforcement, switches the primary adapter to `foundry_openai` (section 5), where structured outputs are documented for `gpt-5-mini`. This is a configuration change.

## 4. Agent sync, lock file and drift check

`make agents-sync` (run by the owner, Phase 4b) for each `active` prompt:

1. Builds the definition and computes `definition_hash = sha256(canonical_json({model, instructions, text_format, tools: []}))`.
2. If the hash differs from `config/foundry_agents.lock.json`, calls `create_version` and writes the returned version.
3. Writes the lock entry: `{agent_name, version, prompt_name, prompt_version, prompt_hash, definition_hash, deployment, structured_output, synced_at}`. The lock file is committed; it contains no secrets.

**Drift check, at runtime, before calls:** the adapter fetches the pinned version's definition (cached for 60 seconds, `model_gateway.settings.drift_cache_s`), recomputes `definition_hash` and compares it with the lock. It raises `AgentDrift` on:
- a mismatch;
- a missing version (404);
- a lock whose `prompt_hash` differs from the prompt now in `prompts/`.

`AgentDrift` escalates (`agent_drift`) and **never** fails over (F01). The runtime never creates or edits agents.

## 5. Direct gateway (`foundry_openai`)

Same deployment, without an agent: `responses.create(model=<deployment>, instructions=<system.md>, input=<user>, text={"format": {type: "json_schema", ...}}, store=False)`. Structured outputs are documented for `gpt-5-mini` 2025-08-07 on the Responses API ([structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)).

It is an **alternative primary**, selected in `modules.yaml` (`model_gateway.adapter: foundry_openai`). It is not an automatic failover target: D17 defines the only failover, to Haiku. It is used for A/B comparison and as the fallback if agent structured outputs prove unreliable.

## 6. Output validation and retry (both routes)

- Every response is parsed as JSON and validated against `prompts/<name>/<version>/schema.json` (the full schema) and the pydantic model.
- **Provider schema:** Azure strict mode requires every field `required`, `additionalProperties: false`, at most 5 nesting levels and 100 properties, and does not support `minLength`, `pattern`, `minimum` or `minItems` ([structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)). `infra/prompts.py` derives a provider schema by dropping unsupported keywords and turning optional properties into required-and-nullable. Our code still enforces the dropped constraints (`maxLength`, `minItems`, enums) with the full schema. Stage A4 reviews each prompt schema against this.
- **Invalid output:** one retry with the identical request; still invalid -> `InvalidModelOutput` -> escalate (no failover, F01). Also invalid:
  - a response cut short by `max_output_tokens` (reasoning tokens count towards it: [reasoning](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning));
  - an Anthropic `stop_reason` of `refusal` or `max_tokens` ([Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)).
- **Sampling parameters:** `temperature` is not sent on either route. The Anthropic API version 2026-10-21 removed `temperature`, `top_p` and `top_k` and returns 400 if they are sent ([model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)). For `gpt-5-mini` the adapter sends only `max_output_tokens`. The `temperature: 0` in `prompts/*/meta.yaml` is ignored by the adapters, and A4 removes it from the meta files (open item for A4).

## 7. Error mapping (Foundry routes)

| Provider signal | Adapter raises | Then |
|-----------------|----------------|------|
| HTTP 400 with code `content_filter` | `GuardrailBlocked` | escalate `guardrail_blocked`, no failover |
| response `content_filters[*].blocked = true` | `GuardrailBlocked` | same |
| `content_filters` annotations with `jailbreak` / `indirect_attack` detected but not blocked | returned in `ModelResponse.screen_annotations` | `foundry_guardrails` InputScreen turns them into flags (I15) |
| 429 `rate_limit_exceeded` (header `retry-after-ms`) | after 2 in-adapter retries honouring `retry-after-ms`: `TransientPortError(reason=rate_limited)` | F01 failover |
| 429 capacity / temporary adjustment | `TransientPortError(reason=unavailable)` | F01 failover |
| 429 `regional_session_quota_exceeded`, or quota exhausted | `TransientPortError(reason=quota_exceeded)` | F01 failover |
| 401, 403 | `AuthPortError` | escalate `module_unavailable:model_gateway`, ERROR log, no failover |
| 404 agent or version | `AgentDrift` | escalate, no failover |
| 410 Gone (retired model) | `PermanentPortError(410)` | escalate, no failover; ERROR log |
| 500, 502, 503, 504, timeout, connection error | after 2 in-adapter retries: `TransientPortError(reason=unavailable)` | F01 failover |
| `incomplete` status for content filtering | not documented on the pages read; treated as `GuardrailBlocked` if `incomplete_details.reason` contains `content_filter`, otherwise `InvalidModelOutput` | — |

Sources for this table: [responses](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses), [quota](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota) (429 types and the `retry-after-ms` / `x-ratelimit-*` headers), [limits](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions) (agent 429 codes).

Model calls have no side effects, so retrying them is always safe. They only cost money, which every attempt records in `model_calls`. Timeout per call: `llm.call_timeout_s` (default 120 s, because reasoning models can take tens of seconds); a timeout counts as `TransientPortError(unavailable)`.

## 8. Guardrails (O2 decided by default: observe the deployment's annotations)

- The owner attaches a content filter with **Prompt Shields** to the deployment. User prompt attack detection is on by default; **document attack detection is off by default** and must be turned on, because seller text is untrusted content ([Prompt Shields](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/content-filter-prompt-shields)).
- The `foundry_guardrails` InputScreen observes the `content_filters` annotations that come back on each response. It makes no separate Prompt Shields API call, which was the smaller option in O2.
- Annotations only add caution (I15). A block is `GuardrailBlocked`.
- "Guardrails for Agents" is **preview** ([GA overview](https://learn.microsoft.com/en-us/azure/foundry/concepts/general-availability)) and is not used; deployment content filters are what the design relies on.
- On the Haiku route there are no Foundry annotations; the rules-based screen runs alone (ARCHITECTURE section 12).

## 9. Tracing (`otel_foundry`, preview, never required by `make verify`)

- Our own spans and the allow-listed attributes in `observability.md` section 3, exported with `azure-monitor-opentelemetry` to Application Insights (`APPLICATIONINSIGHTS_CONNECTION_STRING`).
- The SDK's own GenAI instrumentation is an "experimental preview feature" that needs `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` ([client-side tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-client-side)). It is **not enabled**.
- The adapter also sets `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` explicitly (the default is false: [client-side tracing](https://learn.microsoft.com/en-us/azure/foundry/observability/how-to/trace-agent-client-side)), so no library can record content by accident (I14).
- **No automatic instrumentation.** HTTP and web-framework auto-instrumentation would emit full URLs (mailbox, `$filter` with the idempotency key, JQL) outside the allow-list. The adapter disables every automatic instrumentation the exporter offers and installs a span processor that drops any span or attribute not on the `observability.md` section 3 allow-list (defence in depth). The exact exporter options are read from the `azure-monitor-opentelemetry` documentation in Phase 4b (not read in A3; recorded as risk F13).
- Portal tracing is GA for prompt agents ([GA overview](https://learn.microsoft.com/en-us/azure/foundry/concepts/general-availability)). It is useful to look at, not relied on.

## 10. Cost, `model_calls` and spend caps

- Each call writes a `model_calls` row: input, cached input and output tokens from the response usage; latency; `cost_usd_micros` computed from `config/pricing.yaml`. Reasoning tokens are billed as output ([reasoning](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning)).
- Rules M01-M05 (`policy-rules.md` section 5) run before each call, on an estimate (prompt tokens + `max_output_tokens` at output price).
- **Prices:**
  - `gpt-5-mini` Global: the official pricing page renders "$-" for this model ([Azure OpenAI pricing](https://azure.microsoft.com/en-us/pricing/details/azure-openai/)), so the price is **unverified**. The owner reads it from the Azure portal (deployment pricing or the pricing calculator) at provisioning and records the source.
  - `claude-haiku-5-5`: $0.10 per MTok input and $0.50 per MTok output for prompts up to 100K tokens; cache hits $0.01 per MTok ([Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)).
- Start-up refuses a route with no price row, so caps always work.
- A call that times out or fails without reported usage is still recorded, with a conservative cost estimate (rendered prompt input tokens plus `max_output_tokens` at the output price), so M03/M05 never undercount spend.

## 11. Evaluation and red teaming (Phase 6)

- **Local gates are authoritative** (O3); Foundry evaluations are supplementary. Evaluations are GA, some evaluators preview ([GA overview](https://learn.microsoft.com/en-us/azure/foundry/concepts/general-availability)).
- **Red teaming:** GA as a capability, but cloud red teaming runs only in East US 2, France Central, Sweden Central, Switzerland West and North Central US, **not South India**. Prompt agents are a supported target ([AI Red Teaming Agent](https://learn.microsoft.com/en-us/azure/foundry/concepts/ai-red-teaming-agent)). The SDK exposes it under `.beta.red_teams` "(preview)".
- Open decision O14 asks how to red-team. Default: run our own injection corpus (`test-charter.md` section 5), extended, as a local eval against the live South India agents, and skip Foundry cloud red teaming.

## 12. Secondary route: Anthropic API, Haiku only (`modules/model_gateway/anthropic_direct`)

| Item | Spec |
|------|------|
| Model | `claude-haiku-5-5`: Active, retirement "not sooner than October 7, 2027" ([models overview](https://platform.claude.com/docs/en/about-claude/models/overview)). Do not use `claude-haiku-4-5-20251001`: its tentative retirement ("not sooner than October 15, 2026") has passed ([model deprecations](https://platform.claude.com/docs/en/about-claude/model-deprecations)). Start-up refuses any id not matching `claude-haiku-*` (M06) |
| Auth | `x-api-key` from `ANTHROPIC_API_KEY`, read only by this adapter's settings loader (I14) |
| Request | Messages API, `system` = rendered `system.md`, one user message, `max_tokens`, structured output via `output_config.format: {type: "json_schema", schema: <provider schema>}`. Structured outputs are stable and supported for `claude-haiku-5-5`, no beta header ([structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)). No `temperature`/`top_p`/`top_k` (section 6) |
| Errors ([errors](https://platform.claude.com/docs/en/api/errors), [rate limits](https://platform.claude.com/docs/en/api/rate-limits)) | 400 `invalid_request_error` -> `PermanentPortError`, except when the message says a configured spend limit was reached -> `BudgetExceeded(credit_cap_reached)` · 401, 403 -> `AuthPortError` · 402 `billing_error` -> `BudgetExceeded(credit_cap_reached)` · 404, 413 -> `PermanentPortError` · 429 **with** `retry-after` -> `TransientPortError(rate_limited, retry_after_s)` · 429 **without** `retry-after` (a spend cap; retrying fails until access resumes) -> `BudgetExceeded(credit_cap_reached)` · 500, 504, 529 `overloaded_error` -> `TransientPortError(unavailable)` after 2 retries |
| After failure | It is the last route: a transient failure here goes to the rules-only baseline, or a person (FLOW 7b) |
| Data | Synthetic only (I11). Zero data retention is arranged per organisation through Anthropic and is not required for synthetic data ([data retention](https://platform.claude.com/docs/en/manage-claude/api-and-data-retention)) |
| Owner control | Also set a spend limit in the Anthropic Console |

## 13. Owner provisioning checklist (ordered)

Done by the owner; Claude Code never creates cloud resources or reads secrets. Values go into `.env`, never chat. The full click-by-click version is `docs/FOUNDRY_SETUP.md`.

**Before Phase 3 (email and Jira):**
1. Get a Microsoft 365 test tenant (O13) and create one test mailbox (`GRAPH_MAILBOX`) and one test seller address (`TEST_SELLER_EMAIL`).
2. In Entra: register an app (single tenant) and create a client secret (or upload a certificate). Record `GRAPH_TENANT_ID`, `GRAPH_CLIENT_ID`, and `GRAPH_CLIENT_SECRET` or `GRAPH_CLIENT_CERT_PATH`. **Do not** add tenant-wide Graph mail application permissions: access is granted only through Exchange RBAC in the next step (grants are a union, so a tenant-wide grant would void the scoping).
3. As an Exchange administrator, `Connect-ExchangeOnline`, then: `New-ServicePrincipal` for the app; a management scope containing only the test mailbox; `New-ManagementRoleAssignment` for `Application Mail.Send`, `Application Mail.ReadWrite` and `Application Mail.Read` with that scope.
4. Confirm in Entra that the app has no tenant-wide `Mail.*` application permissions.
5. Wait up to 2 hours, then run `Test-ServicePrincipalAuthorization` against the test mailbox (allowed) and another mailbox (denied). Send one test message and record the topmost `Authentication-Results` authserv-id into `email.settings.trusted_authserv_id`; generate the `extended_property_guid` once.
6. Jira Cloud: create a test project (`JIRA_PROJECT_KEY`) whose workflow has the issue type (`Task`), a `Done` status and a `Duplicate` resolution, and the custom fields in `integrations.md` section 2.4. Find their ids with `GET /rest/api/3/field` and put them in `config/modules.yaml`.
7. Create a Jira API token (scoped recommended). Record its expiry date in `docs/PROGRESS.md`. Set `JIRA_BASE_URL` (site URL, or `https://api.atlassian.com/ex/jira/{cloudId}` for a scoped token), `JIRA_EMAIL`, `JIRA_API_TOKEN`.
8. Set `REVIEW_CONSOLE_TOKEN` to a long random value.

**Phase 4 (optional, before Azure):**

9. Create `ANTHROPIC_API_KEY` and set a Console spend limit; `ANTHROPIC_MODEL_HAIKU=claude-haiku-5-5`; `LLM_SECONDARY_SPEND_CAP_USD` (default 10).

**Phase 4b (starts the 30-day Azure clock, D18):**

10. Create the Azure free account, then upgrade to pay-as-you-go the same day.
11. Create an Azure budget with an alert on the resource group before any call.
12. Create a resource group and a Foundry resource and project in **South India**.
13. Check whether `gpt-5-mini` needs the gpt-5 registration; apply if the portal asks.
14. Deploy `gpt-5-mini` with Global Standard and a small quota. Record `FOUNDRY_MODEL_DEPLOYMENT` and `FOUNDRY_PROJECT_ENDPOINT`.
15. Assign yourself **Foundry User** on the project (optional hardening: a separate runtime identity with **Foundry Agent Consumer**).
16. Attach a content filter to the deployment with Prompt Shields user prompt attack **and document attack** detection on.
17. `az login`.
18. Copy the `gpt-5-mini` prices from the portal into `config/pricing.yaml`, with the source and date.
19. Write the credit expiry date into `AZURE_CREDIT_EXPIRES_ON`; leave `LLM_ALLOW_PAID_USAGE=false`.
20. Optional: create Application Insights and set `APPLICATIONINSIGHTS_CONNECTION_STRING`.
21. When Phase 4b says so: `make agents-sync`, then `make foundry-check`.

## 14. Risk list (rate limits, quotas, regions, preview features)

| Id | Risk | Mitigation |
|----|------|------------|
| F1 | Structured outputs on agents: Microsoft docs conflict | Client-side validation always; try at sync; switch the primary to `foundry_openai` by configuration if needed (section 3) |
| F2 | `gpt-5-mini` may need gpt-5 registration | Checklist step 13; `gpt-5.4-mini` (same region, no stated registration) is the configured alternative (O1) |
| F3 | `gpt-5-mini` price not shown on the official page | Owner copies it from the portal; start-up refuses a missing price |
| F4 | Free-trial subscriptions can have zero model quota | Upgrade to pay-as-you-go on day one (D18) |
| F5 | 429 rate limits and regional session quota | In-adapter retries honouring `retry-after-ms`; F01 failover to Haiku; small per-case call budget (M01) |
| F6 | Preview features (SDK GenAI tracing, Guardrails for Agents, red-team SDK) | Not used, or isolated behind `preview: true` adapters, never in `make verify` |
| F7 | Cloud red teaming not available in South India | Local injection-corpus eval (O14) |
| F8 | `store=false` is not a guarantee of zero retention | Synthetic data only (I11) |
| F9 | Unpinned agent calls are undocumented | Always pin the version from the lock file |
| F10 | Model retirement (gpt-5-mini 2027-07-13) | `make foundry-check` prints the model version; the project ends well before |
| F11 | Anthropic removed sampling parameters | The adapter never sends them; A4 removes `temperature` from prompt meta |
| F12 | Agent version cap (1,000 per agent) | One version per prompt version; far below the cap |
| F13 | Exporter auto-instrumentation could leak URLs or content | Disable all auto-instrumentation; allow-list span processor; TC-SEC-03 scans exported spans |
