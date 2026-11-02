# Handoff

Read this first in every session. It is the single source for current state, decisions already made, and the next action. Last updated: 2026-11-02.

## 1. Status

No code yet. Stage A1 (kernel specification) is approved (D27): `docs/spec/` holds the kernel interfaces, schema, state machine, events catalog, orchestrator, executor and traceability table; ADRs 0004-0014 are accepted. Stage A2 (safety and policy specification) is drafted and awaiting owner approval: `policy-rules.md`, `threat-model.md`, `approvals.md`, `money-rules.md`, `test-charter.md`, ADRs 0015-0018. The verdict ordering and credit proof were approved at the checkpoint (D28-D31); an independent review's eight real defects are fixed (schema-level approval binding, credits netted against earlier credits, outcome and clarification messages exempt from contact limits, R10 mandatory, review-code mapping, ops-wait rule, number-validation exemptions).

**Next action: the owner reviews Stage A2** (open decisions O11, O12). After approval: Stage A3 (integrations and Foundry) on Opus: `./scripts/stage-a-opus.sh`, then `/phase-start a3`.

## 2. The project in five lines

- An action-taking agent that takes a shipping weight dispute from "seller disagrees" to a verified outcome, and later finds repeat causes across disputes.
- Code owns the case lifecycle and every decision. The AI reads evidence and proposes one step.
- Real integrations: Outlook (Microsoft Graph) and Jira Cloud, on test accounts. Simulated: ledger and shipment data. All data is synthetic.
- The reasoning runs in Microsoft Foundry Agent Service (tool-less prompt agents on a `gpt-5-mini` deployment in South India) behind a `ModelGateway` port.
- Built as a kernel plus pluggable modules. Cross-case intelligence is built last into a socket that exists from Phase 0.

## 3. Two meanings of "model" (do not mix them up)

| | Building the project | Running the agent |
|---|---|---|
| What | Claude Code sessions | The reasoning calls the agent makes at runtime |
| Models | Opus for design and planning, Sonnet for implementation, Haiku for search | `gpt-5-mini` (Azure OpenAI) in Foundry Agent Service, South India; secondary: Claude Haiku through the owner's Anthropic API |
| Configured in | `.claude/settings.json`, `scripts/`, `docs/MODEL_STRATEGY.md` | `config/modules.yaml`, `FOUNDRY_MODEL_DEPLOYMENT`, `docs/FOUNDRY_SETUP.md` |

## 4. Decisions already made (do not reopen without an ADR and the owner's approval)

| # | Decision |
|---|----------|
| D1 | Modular design: a pure kernel, pluggable modules switched in `config/modules.yaml`, and a domain pack. |
| D2 | Delhivery-style weight disputes are the first (and only) domain pack. The public repo uses the project name and states it is independent and synthetic. |
| D3 | Cross-case intelligence is built in Phase 7. Its socket (ports, null adapters, empty briefing slot, caution signals) exists from Phase 0. Connecting it changes zero lines in `kernel/`. |
| D4 | Real: Outlook and Jira Cloud. Simulated: ledger and shipment data. Synthetic data only. |
| D5 | The AI proposes one of six actions; deterministic policy decides; the executor acts and verifies by read-back. |
| D6 | Every credit needs human approval. In the pilot every message kind also needs approval (review-all); kinds are relaxed later in `policy.yaml`. All checks still run on every message. |
| D7 | Illustrative defaults: reminder at 48 h, decision at 72 h, reopen window 7 days, contact limit 3 per case and 1 per day. A silent seller ends as Closed: no response and is never counted resolved. |
| D8 | A disabled module that a case needs sends the case to Needs human review with reason `module_unavailable:<name>`. |
| D9 | Handed to a human is final for the agent. |
| D10 | Volumetric divisor is configuration (default 5000, from public documentation). All rates and limits are illustrative. |
| D11 | Python, FastAPI, SQLite through SQLAlchemy Core with Alembic, Streamlit for the approval screen, polling for email replies. |
| D12 | Foundry is the model and governance platform behind our ports. Agent Service is used in v1 as tool-less prompt agents, one per runtime prompt, called statelessly by our orchestrator. Agent definitions are synced from `prompts/` and drift-checked. Foundry never owns case state. Entra ID only. |
| D13 | Build process: Stage A on Opus (specs and task cards), then phases on `opusplan` with Sonnet building and Opus reviewing safety-critical work. |
| D14 | Runtime prompts live in `prompts/`, versioned, provider-neutral. |
| D15 | Runtime primary: `gpt-5-mini` (Azure OpenAI, Global Standard), South India, funded by the owner's Azure free-account credit. Claude on Foundry is deferred (Marketplace billing; credits cannot pay for it). |
| D16 | The approvals module and a message review queue arrive in Phase 3 (not Phase 5), because every message needs review in the pilot. Phase 5 extends approvals to credits. |
| D17 | Secondary route: the owner's Anthropic API, Haiku models only (enforced at startup). Used only for availability, rate-limit, quota and credit-window failures. Never for guardrail blocks, drift, invalid output or caps. Own spend cap (default $10). |
| D18 | The Azure free credit is $200 for 30 days from sign-up. Create the account only when Phase 4b starts, upgrade to pay-as-you-go straight away (free trials can have zero model quota; the remaining credit stays valid), and keep `allow_paid_usage` false so spend stops when the credit window ends. Do the Foundry-dependent work (4b live evals, demo recording, Phase 6 Foundry evals and red team) inside that window. |
| D19 | (2026-11-02) An unclear, partial or irrelevant seller reply gets one clarification follow-up naming the issue; if still unresolved, or the agent cannot handle it, a person takes over (`reply_unresolved`). A seller who replied is never closed as "no response". ADR 0014, transitions X14. |
| D20 | (2026-11-02) A withdrawal written in an email needs a person to confirm; only an authenticated portal withdrawal closes a case on its own. ADR 0009. |
| D21 | (2026-11-02) RESOLVED_CREDITED means the credit is verified and the confirmation email is verified as sent. ADR 0012. |
| D22 | (2026-11-02) A late reply reopens a closed (no response / upheld) case within 7 days and at most 2 automatic reopens; otherwise a person decides. Transitions X9. |
| D23 | (2026-11-02) A person can return a case from review to the agent, after resolving every action with an unknown outcome; a case with a verified credit cannot be returned. Transitions X7, T37. |
| D24 | (2026-11-02) Kill switch: a blocked send moves the case to human review; a person returns it after the switch is off. |
| D25 | (2026-11-02) A repeat dispute on a closed claim reopens it inside the 7-day window, otherwise opens a new linked case with extra caution. |
| D26 | (2026-11-02) Illustrative limits accepted: ops timeout 120 h, approval expiry 72 h, 2 automatic reopens, 2 approval rejections per case, 10-minute verification deadline, 1 clarification per case. A3 owns the final config schema. |
| D27 | (2026-11-02) Stage A1 kernel specification approved: docs/spec/ (kernel-interfaces, schema, transitions, events-catalog, orchestrator, executor, traceability) and ADRs 0004-0014 accepted. docs/spec/ now outranks ARCHITECTURE.md and FLOW.md (CLAUDE.md section 18). |
| D28 | (2026-11-02) Verdict ordering ALLOW < REQUIRE_APPROVAL < ESCALATE < DENY, combined by max; model output and patterns only raise a verdict; the proof that a credit is never ALLOW (policy-rules.md sections 1-2). ADR 0015. |
| D29 | (2026-11-02) A credit is proposed only for a reason code proved by code from evidence; the AI's propose_credit is a request, never the basis. ADR 0016. |
| D30 | (2026-11-02) No configured credit amount caps; the structural limit is the outstanding charge; every credit is human-approved. ADR 0016. |
| D31 | (2026-11-02) Low model confidence on propose_credit or uphold_charge escalates to a person (R22). |

## 5. Open decisions (defaults apply until the owner decides)

| # | Question | Default | Decided in |
|---|----------|---------|------------|
| O1 | `gpt-5-mini` versus `gpt-5.4-mini` as the default, and whether Agent Service accepts both in the chosen region | `gpt-5-mini`; compare on cost per case and eval results | Phase 4b |
| O2 | Guardrail integration: observe deployment guardrail results, or call a Prompt Shields API | Smaller of the two, via ADR | Phase 4b |
| O3 | Use Foundry evaluations | Optional and supplementary; local gates are authoritative | Phase 6 |
| O4 | Microsoft Agent Framework | Not used; revisit only through an ADR | Backlog |
| O5 | Graph authentication model | Least privilege on a test mailbox; ADR | Stage A3 |
| O6 | Release gate thresholds | Drafted in Stage A4; approved in Phase 6 | Phase 6 |
| O7 | When to relax message review (which kinds become automatic) | Review all until you decide, after watching real drafts | Owner, any time |
| O8 | Secondary Haiku spend cap and the Haiku model id | $10 and `claude-haiku-5-5`; confirm both against the Anthropic pricing and model pages | Phase 4 |
| O11 | Review console authentication: a local `REVIEW_CONSOLE_TOKEN` secret (ADR 0018). CLAUDE.md I14 calls the Anthropic key "the one permitted API key"; this token is a local session secret, not a provider key | Use the local token; amend I14's wording to say "the one provider API key" | Owner, Stage A2 review |
| O12 | The case_investigator v1 prompt says "wait: only when an outstanding request has not yet reached its due time", which conflicts with D19 (no wait after a seller reply) | Fix in Stage A4 (prompt v1 is unreleased; edit or create v2 per prompts/README.md) | Stage A4 |

## 6. What the owner must do (Claude Code never does these)

| Before | Owner action |
|--------|--------------|
| Stage A | Update Claude Code (`claude update`; v2.1.293 or later covers Opus 5.5, Sonnet 5.5 and Haiku 5.5). Create the repo and copy this pack in. |
| Phase 3 | Create an Entra app registration with minimal mail scopes; a Jira Cloud site, a test project and an API token; a second email address as the test seller. Put values in `.env` (never in chat). |
| Phase 4 (optional) | Create `ANTHROPIC_API_KEY` in `.env` and set a spend limit in the Anthropic Console. Never put it in Claude Code settings or chat. |
| Phase 4b | Create the Azure free account now, not earlier: the 30-day clock starts at sign-up. Upgrade to pay-as-you-go immediately, create a South India Foundry project, deploy `gpt-5-mini` (Global Standard), assign roles, `az login`, create a budget alert, and write the credit expiry date into `llm_primary.credit_expires_on`. Then run `make agents-sync` when Phase 4b tells you to. |
| Phase 6 | Optional: Application Insights connection string. |

## 7. How to run

- Design: `./scripts/stage-a-opus.sh`, then `/phase-start a1` (then a2 to a5, one per session).
- Build: `./scripts/stage-b-build.sh`, press Shift+Tab to enter plan mode, then `/phase-start 0` (then 1, 2, 3, 4, 4b, 5, 6, 7).
- Close a phase: `/phase-verify <n>`, run the `safety-reviewer` subagent where the phase prompt says so, then `/session-handoff`.

## 8. Where things live

| Need | Location |
|------|----------|
| Rules every session must follow | `CLAUDE.md` |
| Architecture, ports, modules, Foundry | `docs/ARCHITECTURE.md`, `docs/spec/` after Stage A |
| Flow, states, orchestrator loop | `docs/FLOW.md` |
| What to do in each phase | `docs/build-prompts/` |
| The agent's own prompts | `prompts/` |
| Model and cost playbook | `docs/MODEL_STRATEGY.md` |
| Azure and Foundry setup for the owner | `docs/FOUNDRY_SETUP.md` |
| Progress and session log | `docs/PROGRESS.md` |
| Parked ideas | `docs/BACKLOG.md` |
| Decisions | `docs/adr/` |

## 9. Risks to watch

- **Scope creep:** every phase has an out-of-scope list; park ideas in the backlog.
- **Model drift:** model aliases float; the settings file pins versions so cost and behaviour do not change mid-project.
- **Preview features:** Foundry tracing instrumentation, red teaming and memory stores are preview; keep them inside adapters and out of `make verify`.
- **Over-trust in synthetic results:** never claim production readiness.
- **Cost:** watch `/usage` after each session and `model_calls` for the runtime agent.
- **Credit window:** the Azure credit expires 30 days after sign-up. Do not sign up early. After expiry, paid usage stays off unless you enable it.
- **Secrets:** the Anthropic API key is the only API key in the project. It lives in `.env` only.

## 10. Glossary

- **Port:** an interface the kernel owns (a socket). **Adapter:** an implementation of it (a plug).
- **Domain pack:** the rules and templates for one domain (here, weight disputes).
- **Idempotency key:** a key that makes a retry return the original result instead of repeating the action.
- **Read-back:** reading the result from the external system after a write to confirm it happened.
- **Outbox style:** record the intended action first, perform it second, verify it third.
- **Caution signal:** an input (such as a pattern) that can only make the system more careful.
- **Guardrail:** a provider-side content check; advisory here, never an authority.

## 11. Working agreement (summary of `CLAUDE.md`)

Plan first and wait for approval. Small tested steps. Stay in the phase. Explain unfamiliar ideas simply, then deeply. Challenge assumptions. Ask at most five questions, with defaults. Never request secrets in chat. Never create cloud resources.

## 12. Next action

Stage A2: safety and policy specification on Opus (in progress), then owner review.
