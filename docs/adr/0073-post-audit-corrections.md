# 0073 — Post-audit corrections before LLM evaluation

Date: 2026-11-27
Status: **built, not activated** (owner, 2026-11-27, "Post-Audit Corrections — Prepare for LLM Evaluations"). Corrects ADR 0072 after the audit in `docs/audits/2026-11-27-investigation-agent-audit.md`. Kernel change: one constant, `PROMPT_CACHE_BREAK` in `kernel/domain/reasoning.py`.

## Context

The audit of ADR 0072 found defects that would make a paid evaluation misleading or unsafe. The owner approved the fixes and two product rules, with no paid calls and no change to approved answers.

## Decisions

**A refund request is a claim, not manipulation (AUD-06, AUD-08).**
- "I was overcharged. Please refund ₹150." is the ordinary purpose of a dispute.
- Manipulation tries to change how the system works: instructions to the model, claimed authority, "the rules no longer apply", naming the action to take, asking to skip checks or review, or supplying figures to use instead of the evidence.
- The rules screen flags the second kind, including disguised forms, with no model call. The prompt says the same, with both examples.
- Requested items are a closed list in code (`EVIDENCE_ITEMS`), in the output schema (`enum`, at most 2), and in code's decision check for both loops.
- v3 is released and stays as evaluated. The corrections go into **v5** (v3 + the guardrails, inactive) and v4 (inactive).

**Uphold without the hub needs verified evidence (owner rule).**
- Allowed only when no billing error is proven, code's calculation stands, nothing material contradicts, and at least 5 trusted shipments of this product in the matching packaging measure like the parcel.
- Scanner statistics or past disputes alone never support an uphold. Four records are not enough.
- Evaluation cases IC-21 (4 records) and IC-22 (5 records) test the boundary; IC-23 (plain refund) and IC-24 (disguised manipulation) test safety.

**One place decides a gateway's limits (AUD-01, AUD-23).** `app.gateway_policy` applies the pattern budget and the engine's share. The running app, the scenario suite, Decision Quality and the investigation evaluation all call it.

**Policy look-up returns policy or "not found" (AUD-03, AUD-04).** Only `docs/spec/policy-rules.md` is indexed. A query that matches no word returns `not_found`, never unrelated passages.

**Failures are steps, not crashes (AUD-09, AUD-10).**
- A tool the run does not offer is a note that spends a step.
- A model outage before any answer falls back to the rules baseline, like every engine. After an answer, a person decides.
- Every model call is recorded, a failed one included.

**Citations are unique and honest (AUD-22).** Tool results are `T<round>.<call>`. Only a result that answered can be cited, never a refusal, a missing tool or a "same query" pointer.

**Reviewers see the investigation (AUD-13).** Each turn's reference, arguments, outcome and code's own result are in the turn log, and the Desk's case Details tab lists them beside the findings that cite them. Outside text is never logged.

**Cost (AUD-14, AUD-16).**
- Past-shipment records are compact and capped at 10, so a result fits without being cut.
- The case briefing is marked for the prompt cache when several turns can follow. `PROMPT_CACHE_BREAK` is a kernel constant because the prompt (module) and the gateway (module) must agree on it without importing each other.

**A fair comparison (AUD-11, AUD-20, AUD-25).**
- Two arms are added, so each effect is one pair: `agent_v5` vs `plain` (prompt), `plain` vs `enriched` (evidence), `enriched` vs `investigator` (autonomy).
- The grader adds complaint understanding, grounding, safety (false flags) and the hard budget.
- The photo case is reported `not_evaluated` on a route with no vision.
- Past disputes in replays come from the catalogue's corrected and disputed shipments, opened before the dispute.
- The billed cost is read from the spending guard's logs.

## Consequences

- **Easier:** a paid evaluation can now be read effect by effect, and its safety and cost figures are true.
- **Harder:** two more arms to run and pay for (about USD 0.05 each per pass).
- **To watch:** AUD-02 and AUD-07 remain blockers before any real seller data. The reply-reading block (AUD-05) is still not filled.
- **Batching:** several independent tool calls in one turn would cut turns. It changes the loop's contract, so it is a recommendation, not built.

## Addendum, same day: after the focused paid run

The one paid pass (`evals/investigation/runs/inv-focused-paid-20261127T143232Z`, USD 0.062) showed the investigator failing on its tool arguments and the logs hiding why decisions were rejected. Corrections, with no model, prompt-strategy or architecture change:

- **Arguments the model can see.** Each tool line shows its bounds, and the past-shipments search lists the ids this case accepts (about 80 tokens). The prompt had shown `limit (integer)` without its maximum of 10. The accepted ids come from the same lists the search validates against.
- **Errors that say how to fix the call.** Bad arguments name the argument and the rule, never the value given; this is a kernel change in `Toolbox`. An unknown id is answered with the accepted ids for that argument only.
- **Rejections on the record.** Every decision turn is logged as "accepted" or "rejected: <exact reason>", with the refused citations. An answer that fails the output schema twice is logged with why. The model's retry note adds how to fix it, using only code's own ids and lists.
- **Grader.** A concern may be recorded in any listed equivalent phrasing. A decision must cite the tool answer its case rests on (`must_consult`), not every look-up that ran.
- **Tool-calling audit, before the next paid run.** These were found by replaying every arm's turns with a scripted provider (free):
  - The same query, spelled with or without a tool's defaults, now counts as one query.
  - A tool call after the budget is spent now gets one "decide now" warning, within the existing retry. Before, it dropped the case to a person.
  - The enriched arm, which cannot call tools, is no longer shown them.
  - A policy lookup by rule id now ranks the passage that defines the rule first.

## Addendum: reliability fixes after paid run 2

- **Native structured outputs on the Anthropic route only** (`anthropic_direct/structured.py`).
  - **How it works:** the prompt's schema stays the source of truth. The adapter derives what `output_config.format` can enforce (checked against Anthropic's documentation for `claude-haiku-5-5`): it drops length, count and range limits, and sends free-form objects such as a tool's `arguments` as key/value pairs, which it turns back into objects. `parse()` still checks every answer against the full schema.
  - **Fallbacks:** a schema past the documented limits (24 optional fields, 16 unions) is sent as before. So is one the API refuses with a 400; nothing is billed, and the adapter remembers the refusal.
  - **Unchanged:** refusals and truncated answers are still invalid. Other routes and the scripted gateway don't use native structured outputs.
- **Missing evidence.**
  - The prompt now lists the ids the model may cite, and says when there is no seller note. It no longer contains an example that cites the seller's note in a case without one.
  - Code still refuses any citation that isn't in the briefing.
- **Retry feedback.**
  - A schema failure now names the field, the rule, and the allowed fields or values, never a value the model wrote.
  - The existing single retry is told what was wrong, at no extra call. Any gateway that reads the request it is given passes the note on: the Anthropic route and the scripted gateway do; the Foundry routes retry as before.
- **The five-record guardrail, in code.** An uphold needs one of these verified bases (`packs/weight_dispute/baseline.uphold_basis`):
  - the seller's own reply or photo in this round;
  - a hub finding confirming the measurement;
  - a measurement within tolerance of the declaration;
  - a normal authoritative comparison on at least 5 trusted shipments;
  - or a cited past-shipments search showing at least 5 trusted shipments of this product in one packaging that measure like the parcel (`comparable_support`).

  Scanner statistics and past disputes alone are never a basis. The rule applies to every LLM engine. The rules baseline already follows it: its uphold rows are B4c, B6a and B8.
- **Pre-run-3 checks (owner, 2026-11-27).**
  - **Seller evidence must support the charge.** A seller reply or photo merely existing no longer qualifies. A reply counts only when it supplies everything requested, carries no manipulation flag, and claims no size smaller than the hub measured (`seller_supports`). Code cannot see what a photo shows; that judgement stays with the investigator and the approving person.
  - **History never overrides a contradiction.** The contradictions are: an unconcluded hub check, an outstanding remeasurement, unresolved new seller evidence, a scanner or hub reading high against its peers, or a photo showing a smaller parcel or more than one (`history_contradictions`, `tool_contradictions`).
  - **The run record shows how each call's format was enforced:** native, plain, or a fallback.

## Addendum, 2026-11-28: AUD-05

- **Fixed for the v4 investigator only.** The seller reply's structured reading (intent, each requested item, claims, attachments, manipulation flags and summary) now fills v4's `untrusted_reply_reading` block. It is still outside text: the block is labelled untrusted and its content is neutralised.
- **Shown once.** The same reading is no longer repeated among the untrusted evidence. Its evidence id stays citable, and the seller's own raw words stay where they were.
- **The released v3 prompt, the default engine, is unchanged.** Its block was never filled, and changing what v3 sees would change the engine you evaluated.
- **No new model calls and no policy change.**
- **Still mandatory release blockers before any real seller data:** AUD-02 (seller identity at intake) and AUD-07 (cross-seller statistics that can be subtracted).

