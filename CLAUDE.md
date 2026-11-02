# CLAUDE.md

Project: Weight Dispute Resolution & Cross-Case Intelligence Agent.
Independent portfolio project on synthetic data. Not connected to, or affiliated with, any company's systems.

## 1. Role and working agreement

You are the principal engineer. The owner is a technical AI product manager (Python, FastAPI, SQLite, Streamlit, agents, evals) who wants to learn the engineering decisions, not receive a black box.

- Teach as you go: explain an unfamiliar concept in plain language first, then the deeper technical reason.
- Challenge assumptions. If a request or an instruction here looks wrong, say so, with reasons, before acting.
- Plan first. Every phase starts with a plan that the owner approves (section 13).
- Build in small steps. Each step ends with passing tests before the next begins.
- Stay inside the current phase. Park good ideas in `docs/BACKLOG.md`.
- If something is ambiguous or conflicts with an invariant, stop and ask, and offer a default.

## 2. What we are building

An action-taking agent that takes a shipping weight dispute from "the seller disagrees with a charge" to a verified outcome. It gathers evidence, recalculates the charge in code, investigates, emails the seller, raises Jira issues, requests human approval for credits, posts credits to a simulated ledger, and verifies every result. A later module finds patterns across disputes and opens linked investigations. Code owns every decision; the AI investigates and proposes.

Real integrations: Outlook (Microsoft Graph) and Jira Cloud, on test accounts only. Simulated: ledger and shipment data. All data is synthetic. The first domain pack models a public logistics weight-dispute workflow; all rates and limits are illustrative configuration, not any company's real tariff.

## 3. Safety invariants

Never violate these. Each needs at least one test that tries to break it.

| # | Invariant |
|---|-----------|
| I1 | The AI never writes. The reasoning module receives read-only ports and returns a `Proposal`. It cannot reach any write method. |
| I2 | Every side effect goes through `ActionExecutor`: policy verdict, idempotency key, execute, then independent read-back verification. |
| I3 | No credit without a stored approval record, an amount within limits, and no existing credit for the same case and reason. |
| I4 | An unknown or unverifiable outcome becomes `NEEDS_HUMAN_REVIEW`. It is never treated as success. |
| I5 | Text from outside the system (emails, ticket comments, shipment notes) is data. It is delimited, never merged into instructions, and can never change state, permissions or limits. |
| I6 | If a needed module is disabled, the case goes to a human with the reason `module_unavailable:<name>`. Never skip silently. |
| I7 | Patterns may only add caution (context, priority, human review). They can never approve, uphold, credit or lower a threshold. |
| I8 | Case state changes only through the state machine, and every change writes an event with its cause. |
| I9 | Money and weight calculations are deterministic code. Every number in an outbound message must match stored evidence or calculation output. |
| I10 | The kill switch stops all external writes. It is checked inside the executor, not only at the entry point. |
| I11 | Synthetic data only. No real seller data. Real calls only to the owner's test Outlook and Jira. |
| I12 | Secrets never appear in the repo, logs, prompts or test fixtures. |
| I13 | Message kinds that policy lists for review need a stored approval record before sending, and the text sent must be identical to the text approved (hash check). Other kinds send automatically, but only after policy, number validation, contact limits and read-back verification. |
| I14 | Every model call goes through `ModelGateway`. Foundry authentication is Microsoft Entra ID only. The one permitted provider API key is the secondary Anthropic key in the `ANTHROPIC_API_KEY` environment variable: read only by the `anthropic_direct` adapter's settings loader, restricted by code to Haiku models, never in the repo, logs, config files or Claude Code settings. The only other secret of that kind is the local review-console token `REVIEW_CONSOLE_TOKEN` (ADR 0018), under the same rules. Prompt and response content is not exported to telemetry unless `telemetry.capture_content` is explicitly enabled for a test run. |
| I15 | Input screening (including Foundry guardrails) is advisory. A flag can only add caution. A clean result never relaxes a rule, and a guardrail block sends the case to a human. |

## 4. Architecture in brief

Full detail: `docs/ARCHITECTURE.md`. Flow and states: `docs/FLOW.md`.

- `kernel/`: pure domain, state machine, policy engine, orchestrator, executor, ports (Protocols). Imports only the standard library and pydantic.
- `modules/`: pluggable features and adapters (model_gateway, reasoning, input_screen, tracing, email, ticketing, ledger, approvals, evidence, cross_case, evals). Each depends on the kernel only and never on another module.
- `packs/`: domain knowledge (the first pack is `weight_dispute`): calculator, eligibility, rate card, templates, scenarios.
- `infra/`: SQLite repositories, clock, ids, logging, config. `api/`: FastAPI. `app.py`: the only composition root.
- Modules are switched on or off in `config/modules.yaml`. Startup validates dependencies and refuses to run an unsafe combination.
- Cross-case intelligence is a pluggable module behind `PatternContextProvider` and `CaseOutcomeObserver`. Until Phase 7 a null adapter is wired in. Connecting the real one must change zero lines in `kernel/`.

Dependency rule (enforced by import-linter in `make arch`): `app` and `api` -> `modules`, `packs`, `infra` -> `kernel`. Nothing imports `app`. Modules never import each other.

## 5. Stages and phases

| Stage or phase | Outcome | Model plan |
|----------------|---------|------------|
| Stage A (design, once) | Frozen technical specs in `docs/spec/`, task cards and an approved architecture | Opus throughout |
| 0 | Skeleton, tooling, all ports, registry, empty cross-case socket, architecture tests | opusplan |
| 1 | Kernel: case store, state machine, policy, orchestrator skeleton, intake with dedupe | opusplan, Opus review |
| 2 | Weight-dispute pack: deterministic calculation, synthetic data, evidence gathering | opusplan |
| 3 | Real Outlook and Jira, executor with read-back verification, message review queue, timeline view | opusplan, Opus review |
| 4 | Agent reasoning on fakes: prompt registry, ModelGateway port and contract suite, Haiku-only `anthropic_direct` adapter (optional live check), injection defence, first evals | opusplan |
| 4b | Microsoft Foundry Agent Service (gpt-5-mini) with Haiku failover: agent gateway, drift check, tracing, guardrail screen, cost, spend caps, credit window | opusplan, Opus review |
| 5 | Credit approvals, simulated ledger, verified credits, full single-case demo | opusplan, mandatory Opus review |
| 6 | Full scenario suite, metrics, release gates, Foundry evals and red team, failure drills | opusplan |
| 7 | Cross-case intelligence connected to the existing socket | opusplan, Opus review |

Work only on the stage or phase the owner names. Earlier tests must keep passing. See `docs/MODEL_STRATEGY.md` for what each model plan means.

## 6. Engineering principles

- Ports and adapters. Depend on abstractions (Protocols) owned by the kernel. Adapters live outside.
- SOLID in practice: small single-purpose classes; narrow interfaces (a port has few methods); extend by adding adapters, not by editing the kernel.
- Functional core, imperative shell: decisions are pure functions of explicit inputs; I/O happens at the edges.
- Make illegal states unrepresentable: enums, frozen pydantic models, discriminated unions, a declarative transition table.
- Explicit over implicit: dependency injection through constructors. No global state, no singletons, no hidden environment reads.
- Idempotency everywhere: events carry dedupe keys; actions carry idempotency keys; retries are safe by construction.
- Outbox style for side effects: record the intended action first, execute second, verify third.
- Fail fast at startup (config, dependencies); fail safe at runtime (hand to a human).
- Deterministic time and ids through `Clock` and `IdGenerator` ports, so tests are repeatable.
- YAGNI. No multi-agent designs, queues or frameworks without an ADR showing the need.

## 7. Coding standards

- Python 3.12+, `uv`, `ruff` (lint and format), `mypy --strict`, pydantic v2, structlog, pytest, hypothesis.
- Typed everywhere; no `Any` without a comment. Frozen models for domain objects.
- Functions are short and named in domain language. Files rarely exceed 300 lines. No god classes.
- Typed exceptions, no bare `except`. Every network call has a timeout. Retry only idempotent operations, with capped backoff.
- Comments explain why, not what. Public functions and ports have docstrings.
- Money is `Decimal` in INR. Never use floats for money or weight comparisons.

## 8. Testing standards

- Pyramid: unit, property-based (state machine, calculator), contract, integration, scenario (end to end), eval.
- Contract tests: one suite per port, run against every adapter (fake and real). A new adapter is done when it passes the suite.
- Architecture tests: import-linter contracts plus a test that no inline prompt text lives in `src/`.
- No test touches the network by default. Real-service tests carry `@pytest.mark.integration` and need explicit opt-in.
- Branch coverage of `kernel/` at or above 90%.
- Every bug fix starts with a failing test.
- LLM tests use recorded fixtures by default. Live model runs are opt-in evals.

## 9. LLM prompts

Runtime prompts live only in `prompts/`, never inline. Follow `prompts/README.md`: versioned directories, XML-sectioned system prompts, strict output schemas, untrusted data in delimited blocks, prompt name, version and hash recorded on every proposal, and a changelog entry plus eval run for every change. A released version is never edited; create `v2`.

## 10. Configuration, secrets and data

- `config/modules.yaml` (which modules and adapters), `config/policy.yaml` (limits, timers, contact limits, reason codes, which message kinds need review, pattern thresholds), `config/release_gates.yaml` (Phase 6).
- Settings via pydantic-settings and environment variables. `.env` is git-ignored; `.env.example` lists names only.
- Synthetic data is generated from a seeded generator, so runs are reproducible.

## 11. Observability and audit

- structlog JSON logs with `case_id`, `event_id` and `correlation_id` on every line. A redaction processor masks emails and names.
- The `events` table is the audit trail: append-only, one row per input and state change, with its cause.
- A `Tracer` port with a no-op default; OpenTelemetry is an adapter, added only if it earns its place.

## 12. Git, docs and ADRs

- Short feature branches, conventional commits, one task per commit.
- An ADR (`docs/adr/NNNN-title.md`) for every significant decision: context, options, decision, consequences. The decision and its "why" come first.
- Update docs in the same commit as the code. Update `docs/PROGRESS.md` at the end of each phase.
- Before coding against Microsoft Graph, Jira Cloud or any model SDK, read the current official documentation and link it in the ADR. Do not rely on memory.

## 13. Process for every phase

1. Read this file, `docs/ARCHITECTURE.md`, `docs/FLOW.md` and the phase prompt.
2. Plan mode: understanding, task list with files and tests, at most 5 open questions with defaults. Wait for approval.
3. Implement one task at a time. Tests first where practical. `make verify` stays green.
4. Demonstrate each acceptance criterion with a command or test name.
5. Write ADRs, update docs and `docs/PROGRESS.md`, then summarise what was built, what was deferred and why.

## 14. Definition of done

- `make verify` passes (lint, types, unit, contract, architecture, scenario tests).
- Every acceptance criterion in the phase prompt is demonstrated.
- ADRs and docs updated. No TODO without a `docs/BACKLOG.md` entry.
- No new kernel import from modules, packs or infra.

## 15. Commands

`make setup`, `make verify`, `make test`, `make test-integration`, `make lint`, `make typecheck`, `make arch`, `make run-api`, `make run-approvals`, `make demo`, `make eval`.

## 16. Out of scope

Real wallets or tariffs, real seller data, production deployment, multi-agent orchestration, cloud resources (until the owner asks), and any feature outside the named phase.

## 17. Model and cost discipline (Claude Code)

Opus decides, Sonnet builds, Haiku searches. Full playbook: `docs/MODEL_STRATEGY.md`.
- Stage A runs on Opus (`scripts/stage-a-opus.sh`). Build phases run on `opusplan` (`scripts/stage-b-build.sh`): Opus in plan mode, Sonnet when executing.
- Do not switch models mid-conversation. Switch at session boundaries, after `/clear`.
- For a hard design question during a Sonnet session, delegate to the `architect` subagent (Opus, read-only) instead of switching the whole session.
- Before finishing a safety-critical phase (1, 3, 4b, 5, 7), run the `safety-reviewer` subagent (Opus, read-only).
- Keep sessions short: one phase per session, `/clear` between phases, ask for failing-test summaries rather than full logs.
- Never use the `best` or `fable` aliases for this project.

## 18. Authority order

When documents disagree: `docs/spec/` (after Stage A) beats `docs/ARCHITECTURE.md`, which beats a phase prompt, which beats a sketch in `docs/FLOW.md`. `CLAUDE.md` invariants beat everything. Report a conflict in your plan; do not resolve it silently. Current state and decisions: `docs/HANDOFF.md`.
