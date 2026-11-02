# Phase 4b task cards — Foundry integration

Stage A5. Source prompt: `docs/build-prompts/phase-4b-foundry.md`. Phase outcome: the reasoning path calls prompt agents in Foundry Agent Service through `ModelGateway` with Entra ID, and fails over to the Haiku-only Anthropic route only for availability, rate-limit, quota and credit-window errors. Agent definitions are built from the repo's prompt files and drift-checked. Calls are traced without content, metered against spend caps, screened by an advisory guardrail, and any guardrail block, budget breach, spend-cap breach or drift ends in human review.

**Phase defaults:** Sonnet, effort medium. **Safety-critical phase** (`CLAUDE.md` section 17): run `safety-reviewer` before closing it.

**The clock matters.** D18: the Azure free credit is USD 200 for 30 days from sign-up. The owner creates the account **when this phase starts, not earlier**, upgrades to pay-as-you-go immediately, and writes the expiry into `llm_primary.credit_expires_on`. The Foundry-dependent work — 4b live evals, the demo recording, the Phase 6 Foundry evals and red team — must fit inside that window. Order the cards so the live-call ones (P4b-03, P4b-11, P4b-10) are reached early.

**Owner prerequisites:** a South India Foundry project, a `gpt-5-mini` deployment (Global Standard), role assignments, `az login`, a budget alert, and `make agents-sync` when this phase says so. Claude Code never creates cloud resources (`CLAUDE.md` section 16).

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P4b-01 | Foundry scope ADR and documentation check | M | — | |
| P4b-02 | `model_calls` migration and recording | M | P1-01 | |
| P4b-03 | `FoundryAgentServiceGateway` | L | P4-04, P4b-01 | yes |
| P4b-04 | Agent sync, lock file and drift check | L | P4b-03 | yes |
| P4b-05 | `FoundryOpenAiGateway` | M | P4b-03 | |
| P4b-06 | `FailoverModelGateway` and the trigger matrix | L | P4b-03, P4-10 | yes |
| P4b-07 | Budgets as kernel policy rules | L | P4b-02 | yes |
| P4b-08 | Credit window and spend reporting | M | P4b-07 | yes |
| P4b-09 | `FoundryGuardrailsScreen` | M | P4b-03 | yes |
| P4b-10 | `OtelTracer` and the content policy | M | P4b-02 | yes |
| P4b-11 | Live evals and the model-choice ADR | L | P4b-03, P4-11 | |
| P4b-12 | Secret-scanning test | S | — | yes |
| P4b-13 | `make foundry-check` and setup documentation | S | P4b-03 | |

---

## P4b-01 — Foundry scope ADR and documentation check

**Files:** `docs/adr/00NN-foundry-scope.md`.

**Implements:** D12, `foundry.md`, `CLAUDE.md` section 12 ("read the current official documentation and link it in the ADR; do not rely on memory").

**Build:** record the scope — Agent Service tool-less prompt agents, stateless calls, drift check, fallback gateway, and what is deferred. **Read the current Microsoft documentation and link it.** Confirm and record: whether `gpt-5-mini` and `gpt-5.4-mini` are accepted by Agent Service in South India (open decision O1); response-storage control; and the model's **retirement date** — the phase prompt forbids choosing a retired or soon-retired model.

**Tests first:** not applicable.

**Acceptance:** ADR written with live documentation links; O1 either resolved or explicitly deferred to P4b-11.

**Size:** M. **Model:** Sonnet, effort medium. **Stop and ask:** if the documentation contradicts D12 or `foundry.md`, that is a frozen spec — raise an ADR and wait (`escalation-guide.md`).

---

## P4b-02 — `model_calls` migration and recording

**Files:** `migrations/versions/00NN_model_calls.py`, `src/dispute_agent/infra/sqlite/model_calls.py`, `config/pricing.yaml`.

**Implements:** `observability.md` section 4, `foundry.md` section 7, I14.

**Build:** record per call: case id, purpose, route, deployment, agent name and version, prompt name, version and hash, token counts, latency, and estimated cost from `config/pricing.yaml`. Prices are **configuration**, read from the Azure pricing page, not hard-coded. **No prompt or response content** unless `telemetry.capture_content` is explicitly enabled for a test run (I14).

**Tests first:** `test_model_calls_no_content.py` — with `capture_content` false, a call with sentinel content writes a row containing none of it.

**Acceptance:** `pytest tests/unit/infra/test_model_calls.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P4b-03 — `FoundryAgentServiceGateway`

**Files:** `src/dispute_agent/modules/model_gateway/foundry_agent_service/`.

**Implements:** `foundry.md` sections 1-6, ADR 0021 (pinned version, stateless, validated in our code), D12, I14.

**Build:** `AIProjectClient` with `DefaultAzureCredential` — **Entra ID only, no API key support in code** (I14). Create agent versions with `PromptAgentDefinition`: model is the deployment name from `FOUNDRY_MODEL_DEPLOYMENT`, instructions are the rendered system prompt. Call through the project's OpenAI client Responses API with an agent reference, **by pinned version** (ADR 0021). Return text; parse and validate against `schema.json` with pydantic; retry once on invalid output, then raise the typed error. Extract token usage and latency. Timeouts everywhere; retry only 429 and 5xx with capped backoff.

Foundry never owns case state (D12) — calls are stateless, and our orchestrator holds everything.

**Tests first:** the P4-04 contract suite against this adapter (`integration`). Plus `test_no_api_key_path.py` — no code path accepts a Foundry API key.

**Acceptance:** `make test-integration` with the Foundry project configured.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I14).

---

## P4b-04 — Agent sync, lock file and drift check

**Files:** `scripts/agents_sync.py`, `agents_check.py`, `agents.lock`, Makefile targets.

**Implements:** ADR 0021, D12 (definitions synced from `prompts/` and drift-checked), `foundry.md` section 3.

**Build:** `make agents-sync` is **owner-run and explicit** — it creates agent versions in Foundry from the repo's prompt files and writes `agents.lock` with each agent's name, version and the prompt hash it was built from. `make agents-check` compares the lock file with the current repo prompt hashes. Drift **at runtime** raises `AgentDrift`, which ends in `NEEDS_HUMAN_REVIEW` with reason `agent_drift` and **never fails over** (F01).

**Tests first:**
- `test_drift_detection.py` — editing a prompt file after sync makes `agents-check` fail and names the prompt.
- `test_drift_escalates.py` — a drifted call escalates `agent_drift` with no failover (scenario S42).

**Acceptance:** `make agents-check` passes after a sync and fails after a prompt edit.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes — drift means the thing running is not the thing reviewed.

---

## P4b-05 — `FoundryOpenAiGateway`

**Files:** `src/dispute_agent/modules/model_gateway/foundry_openai/`.

**Implements:** `foundry.md` section 5.

**Build:** the same deployment through the project's OpenAI client **without** an agent, selectable in `config/modules.yaml`. Used as a fallback and for A/B comparison in P4b-11.

**Tests first:** the P4-04 contract suite against this adapter (`integration`).

**Acceptance:** `make test-integration`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P4b-06 — `FailoverModelGateway` and the trigger matrix

**Files:** `src/dispute_agent/modules/model_gateway/failover/`.

**Implements:** `policy-rules.md` section 6 (F01, as amended by D42), `FLOW.md` section 7b, D17, I14.

**Build:** primary Foundry, secondary `anthropic_direct` (Haiku only). Fail over **only** for: provider unavailable, 429 after retries, quota exceeded, credit window ended. **Never** for `GuardrailBlocked`, agent drift, invalid output after the retry, the LLM budget, or a spend cap on either route (D17: never fail over past a cap — a cap is a limit the owner set on purpose). Write a `MODEL_ROUTE_FAILOVER` event for every failover and record the serving route in `model_calls`.

Per D42: an **M05 secondary spend cap** escalates `credit_cap_reached`; the **secondary failing for an availability reason** falls through to the rules-only baseline.

**Tests first:** `test_failover_matrix.py` — the trigger and non-trigger matrix **exhaustively**: one case per error type against the expected behaviour, including both M05 branches. Plus scenario S23a (Foundry unavailable, Haiku answers) and S42 (caps and drift, `secondary_model_calls: 0`).

**Acceptance:** `pytest tests/unit/modules/model_gateway/test_failover.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes — this matrix is where a wrong cell silently spends the owner's money on another provider.

---

## P4b-07 — Budgets as kernel policy rules

**Files:** `src/dispute_agent/kernel/policy/rules/{llm_budget,credit_cap}.py`.

**Implements:** `policy-rules.md` section 6 (M01-M06), ADR 0017 (budgets and failover in one kernel decorator), `config.md`, I14.

**Build:** `llm_budget` against `llm.max_calls_per_case` — **6 normally, 10 when `cross_case` is enabled**, because two-pass reasoning doubles the investigator call per patterned round (`agent-design.md` section 4). `credit_cap` against cumulative estimated spend from `model_calls`, per route: `llm_primary.spend_cap_usd` (150) and `llm_secondary.spend_cap_usd` (10). A breach ends in `NEEDS_HUMAN_REVIEW` with `llm_budget_exceeded` or `credit_cap_reached`.

These are **kernel** rules, not gateway concerns (ADR 0017), so a module cannot spend past them.

**Tests first:**
- `test_llm_budget.py` — scenario S23b (budget 2, exhausted); and that the limit becomes 10 when `cross_case` is on.
- `test_credit_cap_per_route.py` — each route's cap is counted separately; neither failover crosses a cap.

**Acceptance:** `pytest tests/unit/policy/test_budgets.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes.

---

## P4b-08 — Credit window and spend reporting

**Files:** `src/dispute_agent/kernel/policy/rules/credit_window.py`, `scripts/foundry_spend.py`.

**Implements:** `policy-rules.md` M04, D15, D18, `config.md` (`llm_primary`).

**Build:** after `llm_primary.credit_expires_on`, Foundry calls stop unless `allow_paid_usage` is true (default **false**, so spend stops when the credit window ends, D18). M04 raises `TransientPortError(credit_window_ended)` so F01 may route to the secondary; with no secondary available the case escalates `credit_window_ended`. `make foundry-spend` prints cumulative estimated spend **per route** and days left in the window, and warns at 50% and 80% of each cap.

**Tests first:** `test_credit_window.py` — past the expiry with `allow_paid_usage` false, the primary refuses and the secondary serves; with no secondary, the case escalates.

**Acceptance:** `make foundry-spend` prints the report; `pytest tests/unit/policy/test_credit_window.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes (it is the owner's money).

---

## P4b-09 — `FoundryGuardrailsScreen`

**Files:** `src/dispute_agent/modules/input_screen/foundry_guardrails/`, an ADR.

**Implements:** open decision O2, I15, `policy-rules.md` R23.

**Build:** **decide in an ADR first** between observing guardrail results on the deployment and calling a Prompt Shields API, then implement the smaller one (O2). Results become manipulation flags and caution **only** (I15): a flag may add caution, a clean result never relaxes a rule. A provider block raises `GuardrailBlocked`, ending in `NEEDS_HUMAN_REVIEW` with `guardrail_blocked` and **no failover**.

**Tests first:** `test_guardrail_advisory.py` — a clean screen result changes no verdict (positive control); a flag can only raise one; scenario S22 (block, `secondary_model_calls: 0`).

**Acceptance:** `pytest tests/unit/modules/input_screen -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes (I15).

---

## P4b-10 — `OtelTracer` and the content policy

**Files:** `src/dispute_agent/modules/tracing/otel_foundry/`.

**Implements:** `observability.md`, I14, I12.

**Build:** spans per orchestrator stage carrying `case_id`, state, prompt name, version and hash, agent name and version, verdict and token counts. **Never message content or personal data** (I14). Console exporter by default; Application Insights when `APPLICATIONINSIGHTS_CONNECTION_STRING` is set. Foundry tracing instrumentation is **preview** — keep it inside the adapter and out of `make verify` (`HANDOFF.md` section 9).

**Tests first:** `test_spans_no_content.py` — a traced run with sentinel content and a sentinel secret emits spans containing neither.

**Acceptance:** `pytest tests/unit/modules/tracing -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes (I12, I14).

---

## P4b-11 — Live evals and the model-choice ADR

**Files:** `docs/adr/00NN-model-choice.md`, eval outputs.

**Implements:** open decision O1, `release-gates.yaml`, the phase prompt task 11.

**Build:** run the scenario suite live: rules-only baseline versus `gpt-5-mini`, and `gpt-5.4-mini` if Agent Service accepts it in the region. Add **cost per case** and **latency** to the comparison table and recommend the default in an ADR, resolving O1. **Unauthorised financial actions must remain 0** — that is a hard gate, not an average.

Use this run to recalibrate `cost_per_resolved_case_usd` against real prices (`release-gates.yaml` names risk F3).

**Tests first:** the eval run is the deliverable; it is opt-in (`integration`).

**Acceptance:** the comparison table exists with cost and latency; O1 resolved in an ADR.

**Size:** L. **Model:** Sonnet, effort medium.

**Watch:** this card and P4b-10 must happen **inside the credit window** (D18). If the window is closing, do this before the optional polish cards.

---

## P4b-12 — Secret-scanning test

**Files:** `tests/architecture/test_no_secrets.py`.

**Implements:** I12, I14, the phase prompt's technical requirements.

**Build:** a test that fails if any key-like string appears in code, config, fixtures, CI files, logs or spans. The Foundry route is Entra ID only; the **only** key anywhere in the project is the Haiku key in the environment, plus `REVIEW_CONSOLE_TOKEN`.

**Tests first:** the test is the deliverable. Prove it works by planting a fake key in a scratch file and seeing it fail.

**Acceptance:** `pytest tests/architecture/test_no_secrets.py -q`, and a demonstrated failure on a planted key.

**Size:** S. **Model:** Sonnet, effort medium. **Opus review:** yes (I12).

---

## P4b-13 — `make foundry-check` and setup documentation

**Files:** `scripts/foundry_check.py`, `docs/FOUNDRY_SETUP.md`.

**Build:** one tiny call per configured agent printing deployment, agent version, latency and token counts — never content. Update `docs/FOUNDRY_SETUP.md` with whatever the implementation taught.

**Acceptance:** `make foundry-check` prints the report and no content.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green with fakes only; the contract suite passes for both Foundry adapters and the failover composite; drift is detected; the failover matrix is exhaustively tested; spend and the credit window are reported per route; no secret anywhere; `/phase-verify 4b`; **run `safety-reviewer`**; `/session-handoff`.
