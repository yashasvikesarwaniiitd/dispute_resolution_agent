# 0039 — A read-only Case Explainer on the Claude Agent SDK, and a bounded I14 exception

Date: 2026-11-05
Status: accepted. The owner approved the feature, the I14 exception (read-only, Haiku only) and a USD 0.50 cap shared with ADR 0041 (D60).

## Decision

Reviewers get a **Case Explainer**. They ask a question about a case ("why did this go to a person?") and Claude Haiku answers by choosing among four **read-only** tools:

- `case_overview`
- `case_timeline`
- `case_decisions`
- `policy_lookup` (ADR 0040)

It runs on the **Claude Agent SDK** (`claude-agent-sdk`, 0.2.178), which supplies the tool-using agent loop. The Explainer sits **outside the decision path**: nothing it says is acted on, and it has no way to act.

## Why

A review reason like `policy_escalated` or `R16` tells a new reviewer very little. The answer is spread across the timeline, the proposals, the approvals and the policy text. An assistant that reads those records and explains them, citing its sources, saves the digging without letting anyone, or anything, skip a decision.

## Read-only, in layers

Sources: code.claude.com/docs/en/agent-sdk (custom-tools, python, permissions), read on 2026-11-05.

1. **No built-in tools.** `tools=[]`, so Claude Code's shell, file and web tools are not offered.
2. **Only our tools exist, and each one only reads.**
   - `CaseReader` opens the case database with SQLite's `mode=ro`, so the database itself refuses a write. A test proves it.
   - The policy index is local text.
3. **Anything else is denied, twice over.**
   - `permission_mode="dontAsk"` denies any tool that isn't pre-approved.
   - A `PreToolUse` hook denies any tool outside our four as a backstop. A test runs the hook against `Bash`, `Write`, `Edit`, `WebFetch` and an unknown tool.
4. **No local settings.** `setting_sources=[]` and an empty working folder, so no project or user settings or instructions are loaded.
5. **No path to a writer.** The package imports no executor, write port, channel, unit of work or API module (`tests/architecture/test_agent_libraries.py`).

**Outside text stays data (I5).** The seller's statement is neutralised and returned under `untrusted_seller_statement`. The prompt says such fields are evidence, never instructions. The seller's email address is never returned.

## The I14 exception

I14 sends every model call through `ModelGateway`. The Agent SDK runs its own loop and calls Anthropic directly, so this is a named exception, bounded as follows:

- **Haiku only**, checked by the same `claude-haiku-*` rule as the Haiku failover route (`check_haiku`).
- The key is read by the settings loader and passed to the SDK process only (`options.env`).
- **Spending:** a per-question budget (`max_budget_usd=0.05`) and a lifetime cap (USD 0.25). The cap is checked **before** each question against the Explainer's own usage log (`data/explainer_usage.jsonl`), which holds time, cost, turns and model only, never the question, the answer or any tool output.
- **Built only in `app.py`**, the one file allowed to create a route to a model.

The exception is safe to grant because the Explainer cannot change any case. The gateway's purpose, keeping every decision-shaping call guarded and logged, is untouched.

## Activation

The prompt is `prompts/case_explainer/v1`. Before any paid call, a rule was fixed for its live check (`make explain ARGS="--check"`): three synthetic disputes, one question each, and every answer must name the real state or review reason, cite a source, and recommend no decision.

- **Result:** 3 of 3, USD 0.003. The prompt is now `active`.
- **A product gap it found:** a `REJECTED_INELIGIBLE` case does not record which eligibility rule failed. This is in BACKLOG.

## Consequences

- `make explain ARGS="<case_id> <question>"`, `--list` and `--check`.
- A new dependency group, `agents`, is installed by default so CI tests it. The kernel cannot import it (import-linter).
- The Explainer's answers are a convenience, not a record. The case's audit trail stays the source of truth.
