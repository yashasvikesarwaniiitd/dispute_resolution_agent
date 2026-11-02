# Phase 4b: Microsoft Foundry Agent Service integration

## Role
Principal engineer connecting the agent to Microsoft Foundry Agent Service without moving any decision, state or secret outside our code.

## Context
Phase 4 is done: the reasoning module runs on `FakeModelGateway` and recorded fixtures. Read `docs/ARCHITECTURE.md` section 12 and `docs/FOUNDRY_SETUP.md`. Read the current official documentation before coding and link it in the ADR (do not rely on memory):
- Agent Service prompt-agent quickstart: https://learn.microsoft.com/azure/foundry/agents/quickstarts/prompt-agent?tabs=python
- Region availability for models sold by Azure: https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability
- Foundry feature availability across regions (Agent Service regions): https://learn.microsoft.com/en-us/azure/foundry/reference/region-support
- Quotas and limits: https://learn.microsoft.com/en-us/azure/foundry/openai/quotas-limits
- Foundry Control Plane (tracing, guardrails, evaluation): https://azure.microsoft.com/en-us/products/ai-foundry/control-plane
- The `azure-ai-projects` package (version 2.3.0 or later) README on learn.microsoft.com
The owner has created the Azure resources by following `docs/FOUNDRY_SETUP.md` (region South India, a `gpt-5-mini` Global Standard deployment). You never create Azure resources.


**Task cards: `docs/spec/task-cards/phase-4b.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
The reasoning path calls prompt agents in Foundry Agent Service through `ModelGateway` with Entra ID, and fails over to the Haiku-only Anthropic route only for availability, rate-limit, quota and credit-window errors. Agent definitions are built from the repo's prompt files and are drift-checked. Calls are traced without content, metered per call against spend caps, screened by an advisory guardrail, and any guardrail block, budget breach, spend-cap breach or drift ends in human review.

## Design decision (owner-approved): Agent Service in v1
- One **prompt agent per runtime prompt** (`case_investigator`, `seller_reply_interpreter`, `seller_message_drafter`; `pattern_analyst` in Phase 7). Single purpose, no tools, no agent-to-agent calls, no multi-agent orchestration.
- **Our orchestrator calls them.** Case state, history, decisions and approvals stay in our SQLite store (I8). Agents hold instructions only.
- **Stateless calls:** one response per call, no conversation reuse. Confirm in the docs whether response storage can be switched off, and record the choice in the ADR.
- **Prompts are code.** `make agents-sync` creates a new agent version from `prompts/<name>/<version>/system.md` and writes `config/foundry_agents.lock.json` (agent name, version, prompt hash, deployment). Runtime refuses to call an agent whose lock entry does not match the repo prompt hash (`agent_drift`).
- No tools means no write handle exists for the model (I1). Add tools only through an ADR and the owner's approval.

## Scope
### In scope
`foundry_agent_service` gateway, `foundry_openai` fallback gateway, `FailoverModelGateway` (Foundry then Haiku), credit-window handling, contract suite for the Foundry adapters, agent sync and drift check, `otel_foundry` tracer, `foundry_guardrails` input screen, `model_calls` table, pricing file, budget, spend-cap and credit-window rules, `make foundry-check`, live eval run.
### Out of scope
Claude on Foundry (deferred; see the backlog), hosted (containerised) agents, Foundry evaluations and red teaming (Phase 6), anything that gives Foundry ownership of case state.

## Tasks
1. ADR for the Foundry scope: Agent Service tool-less prompt agents, stateless calls, drift check, fallback gateway, and what is deferred. Record any behaviour you confirmed in the documentation (for example, whether `gpt-5-mini` and `gpt-5.4-mini` are accepted by Agent Service in this region, and response-storage control).
2. The `ModelGateway` contract suite from Phase 4 must also pass for both Foundry adapters and for the failover composite.
3. `FoundryAgentServiceGateway`: `AIProjectClient` with `DefaultAzureCredential`; create agent versions with `PromptAgentDefinition` (model = deployment name from `FOUNDRY_MODEL_DEPLOYMENT`, instructions = rendered system prompt); call through the project's OpenAI client Responses API with an agent reference. Return text, parse and validate it against `schema.json` (pydantic), retry once on invalid output, then raise the typed error. Extract token usage and latency. Timeouts everywhere; retry only 429 and 5xx with capped backoff. No API key support in code.
4. `make agents-sync` (owner-run, explicit) and `make agents-check` (compares the lock file with repo prompt hashes). Drift at runtime raises a typed error that ends in `NEEDS_HUMAN_REVIEW` with reason `agent_drift`.
5. `FoundryOpenAiGateway`: the same deployment through the project's OpenAI client without an agent, selectable in `config/modules.yaml`. Used as a fallback and for A/B comparison.
6. `OtelTracer`: spans per orchestrator stage with `case_id`, state, prompt name, version and hash, agent name and version, verdict, token counts. Never message content or personal data (I14). Console exporter by default; Application Insights when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set. Preview instrumentation only inside the adapter.
7. `model_calls` migration and recording: case id, purpose, deployment, agent name and version, tokens, latency, estimated cost from `config/pricing.yaml` (prices are configuration; read them from the Azure pricing page).
8. Budgets as kernel policy rules: `llm_budget` (`llm.max_calls_per_case`) and `credit_cap` (cumulative estimated spend from `model_calls` against `llm_primary.spend_cap_usd`, default 150, and the secondary route's own cap `llm_secondary.spend_cap_usd`). A breach ends in `NEEDS_HUMAN_REVIEW` with reason `llm_budget_exceeded` or `credit_cap_reached`. `make foundry-spend` prints cumulative estimated spend and warns at 50 and 80 percent of the cap.
9. `FoundryGuardrailsScreen` (`InputScreen`): decide in an ADR between observing guardrail results on the deployment and calling a Prompt Shields API, then implement the smaller one. Results become manipulation flags and caution only (I15). A provider block raises `GuardrailBlocked` and ends in `NEEDS_HUMAN_REVIEW` with reason `guardrail_blocked`.
10. `make foundry-check`: one tiny call per configured agent that prints deployment, agent version, latency and token counts, and never content or tokens.
11. Live evals (opt-in marker): rules-only baseline versus `gpt-5-mini`, and `gpt-5.4-mini` if Agent Service accepts it. Add cost per case and latency to the table, and recommend the default in an ADR. Unauthorised financial actions must remain 0.
12. Update `docs/FOUNDRY_SETUP.md` with anything you learned. ADRs: Foundry scope, guardrail approach, telemetry content policy, model choice.
13. `FailoverModelGateway`: primary Foundry route, secondary `anthropic_direct` (Haiku only). Fail over only for provider unavailable, 429 after retries, quota exceeded or credit window ended. Never for `GuardrailBlocked`, agent drift, invalid output after the retry, the LLM budget or a spend cap (see `docs/FLOW.md` section 7b). Write an event for every failover, and record the serving route in `model_calls`. Test the trigger and non-trigger matrix exhaustively.
14. Credit window: `llm_primary.credit_expires_on` and `llm_primary.allow_paid_usage` (default false). After the expiry date, Foundry calls stop unless the owner sets `allow_paid_usage`; the failover route takes over, or the case escalates with reason `credit_window_ended`. `make foundry-spend` shows spend per route and days left in the window. Each route has its own cap.

## Technical requirements
- The Foundry route is Entra ID only. The only key anywhere is the Haiku key in the environment. Add a secret-scanning test that fails if any key-like string appears in code, config, fixtures, CI files, logs or spans.
- Every network call has a timeout. Retries are capped and only for idempotent calls.
- Default `make verify` uses fakes only. Foundry tests carry `@pytest.mark.integration`.
- Avoid retired or soon-retired models. Check the model's retirement date in the documentation before choosing it.

## Acceptance criteria
- `make foundry-check` succeeds with `az login` credentials and prints no content or secrets.
- The `ModelGateway` contract suite passes for the fake (`make test`) and both Foundry adapters (`make test-integration`).
- Editing a prompt without running `make agents-sync` makes `make agents-check` fail, and a runtime call escalates with `agent_drift` (tests).
- A canary string placed in a seller email never appears in any exported span or log (test).
- A guardrail block, a budget breach and a spend-cap breach each end in `NEEDS_HUMAN_REVIEW` with the right reason (tests).
- Every model call has a `model_calls` row, and `make foundry-spend` agrees with the sum.
- The live eval table shows cost per case and latency for each model tried.
- A failover occurs for each trigger and for no non-trigger (matrix test). Guardrail blocks and agent drift never reach the secondary route (tests).
- After `credit_expires_on`, with `allow_paid_usage` false, no Foundry call is made and the secondary or human review takes over (test).
- The secondary route accepts only Haiku model ids, and no key or content appears in any log or span (tests).

## Deliverables
Adapters, contract suite, sync and check commands, lock file, migration, pricing file, policy rules, make targets, ADRs, updated setup guide.

## Constraints
I1, I5, I8, I12, I14, I15. A model response is data, never an instruction.

## Process
Plan mode first. List the documentation passages you rely on, with links. If anything needs the owner (resource, role assignment, quota, credit question), stop and say exactly what.

## Model and effort
`opusplan`, effort medium. Sonnet builds the adapters. Have the `safety-reviewer` subagent (Opus) review authentication, drift handling, telemetry redaction and the guardrail mapping before finishing.

## Stop and ask if
The deployment, endpoint or role assignment is missing; Agent Service rejects the deployed model in this region; documentation contradicts the contract suite; or the guardrail integration would send untrusted text to a service the owner has not approved.
