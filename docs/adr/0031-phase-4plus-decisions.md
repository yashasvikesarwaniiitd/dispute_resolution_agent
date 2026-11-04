# 0031 — Phase 4+ decisions: guardrails, structured output, injection layers, number words, failover

Date: 2026-11-04
Status: accepted (built under the owner's instruction "complete Phase 4+ and Phase 5")

## 1. Guardrails: observe the deployment's annotations (resolves O2)

The smaller of the two options in `foundry.md` section 8. The Foundry route reads Prompt Shields
detections (`jailbreak`, `indirect_attack`) from the response's `content_filters`, defensively -
an unrecognised shape is ignored, never guessed - and the reasoner adds them to the proposal's
`manipulation_flags` as `prompt_shield:<kind>`. R21 then escalates `manipulation_suspected`.
A provider block (a 400 `content_filter`, or a response stopped by the filter) is
`GuardrailBlocked`: a person, no failover. No separate Prompt Shields call is made. I15 holds:
an annotation can only add caution, and a clean result changes nothing.

## 2. Structured output: our code validates, always

Every route returns text; `output.py` parses it as one JSON object, validates it against the
prompt's `schema.json`, and retries once. Agent Service does not support structured outputs
(ADR 0029), so the provider is never relied on for shape. The reasoner, interpreter and drafter
each add checks only the context can make (menu, cited ids, echoed items, cited numbers) with
one more retry; a rejected answer followed by an unreachable model is invalid output, never a
fallback.

## 3. Injection defence is layered, and tested with a fooled model

1. **The fence**: outside text reaches a prompt only through the registry's `untrusted` argument
   (now also for `ctx.*` prompts), inside a delimited block, through `neutralise`.
2. **The screen**: `RulesBasedInputScreen` flags attack shapes in every outside text before the
   model is asked; `instruction_like` (which hiding, encoding, markup, recipient changes and
   number injection also imply) makes R21 escalate.
3. **The rules**: R20/R21/R22/R23 can only escalate; the menu, R14-R16 (code-derived credit
   amounts) and approvals bound by hash hold whatever the model says.
4. **People**: every message kind still needs approval (D6).

The 12-item corpus runs by the statement, shipment-note and reply routes against a model scripted
to notice nothing: no item produces a credit or an unflagged message.

## 4. Number words are refused in the kernel

R09 now denies a message containing a number word, using one list in `kernel/policy/numbers.py`
shared with the template path. "one" is excluded: it is ordinary English in the drafter's own
examples, and no charge, weight or size is written as "one". This narrows residual risk RR1.

## 5. Failover and caps live in the kernel guard (ADR 0017, as built)

`BudgetedModelGateway` applies M01, M03, M04 and M05 and fails over (F01) only for availability.
The Haiku route is **opt-in** (`secondary` in the builder, `--with-haiku` on a trial): it is never
switched on because an `ANTHROPIC_API_KEY` happens to be exported, since that key may not be this
project's.

## 6. Tracing is allow-listed

`OtelTracer` keeps only the attributes `observability.md` section 3 names for each span kind and
cuts values short; content and secrets have no attribute to ride in on. Console export by
default; Application Insights export is optional and outside `make verify`.

## Deferred

Live evals and the model-choice ADR (P4b-11) need a spend decision by the owner. Record-replay
fixtures (P4-04) are replaced by scripted fakes and live opt-in runs.
