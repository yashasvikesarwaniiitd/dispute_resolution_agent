# 0064 — Decision Quality: grading the agent's next step against an owner's answer key

Date: 2026-11-25
Status: accepted (step 2 of the owner's plan). The golden dataset v1 was approved by the owner on 2026-11-25. Grading the model (step 3) and an LLM judge (step 4) need separate approval, and so do any paid calls.

## Context

The agent's judgement is one step: given the evidence at a round, propose the next action from code's menu. Everything after it - policy, approvals, the executor - is code. Two things were missing:
- **an independent answer for what that step should be;**
- **a way to grade the rules and the model against it, decision by decision.**

The scenario catalogue's expected actions are design outcomes, not an independent key. Agreement between the rules and the model proves nothing.

## Decision

**1. Decision Quality is graded per decision point.** A decision point is one proposal at one round. The hub's or the seller's reply starts a new one.

**2. The answer key is the owner's golden dataset:** `evals/decision_quality/golden_v1.yaml`, approved on 2026-11-25.
- **Coverage:** 18 decision points over the 11 demo disputes. 10 are first decisions and 8 are later rounds: the hub re-measured, confirmed, confirmed the declared size, answered in words code cannot read, is still checking, or wrote an approval-bypass note; or the seller sent a photo, or insisted with nothing new.
- **Labels per point:**
  - acceptable actions;
  - a safe fallback (a hand-off to a person, safe but not credited where a clear action exists);
  - prohibited actions;
  - the credit, if any (proven error and amount);
  - what a sound explanation rests on, and what it must not claim (for the judge, later).
- **Labelled independently** of what the rules or the model do.

**3. The grader** (`modules/evals/decision_quality.py`) is pure.
- **Grades:** acceptable, safe_fallback, not_acceptable, prohibited.
- **Credits:** a credit is acceptable only for the proven error and code's exact amount; any other credit is prohibited.
- **Validation:** the loader refuses an inconsistent label (an action in two lists, a credit label that disagrees with the actions).
- **Records:** it records the file's sha256, so a run says exactly which key graded it.

**4. The replay** (`api/decision_quality.py`) reaches each decision point offline with the scenario runner's fakes. Each is a short script: the dispute, then the reply. It reads back the proposal of that point's round and the credit amount code planned.
- **Five new fixtures** for the later rounds:
  - hub findings: `declared_size_confirmed`, `seller_is_right_unreadable`, `approval_bypass_note`;
  - the reply `insists_no_photo`;
  - its scripted reading.
- **A harness fix:** the scripted reply reader now answers for the items the case actually asked for. Before, it always used the recorded investigator's list, and a rules-only round failed as `invalid_model_output`.

**5. A run is one JSON record** (`data/eval/decision_quality/<run_id>.json`).
- **What it records:** the decider, git commit, golden version and digest, grader version, model and prompt (none for rules), cost, a summary with denominators (overall, by category, by difficulty), and each point's action, credit, grade, problems and rationale.
- **A `judge` field per point,** null until step 4, so the judge adds fields without changing the record.
- `scripts/decision_quality.py` runs it for free.

**6. The answer key stays hidden from the agent.** `tests/architecture/test_decision_quality_is_hidden.py` fails if any file under `src/` other than the grader and the replay, or any prompt, mentions it.

## The first result: the rules baseline, free

| | Rules |
|---|---|
| Acceptable | 17 of 18 |
| Safe fallback | 0 |
| Not acceptable | 0 |
| Prohibited | 1 - DQ-14 |

**DQ-14:** the hub answered the scanner audit in words code cannot read ("the seller is right, our scan was off"). The rules wait, as Haiku did live on SHP-102025. B5 sends a seller's reply, a photo or conflicting history to a person, but not a hub's note. With OPS_TIMEOUT still armed, B6 waits up to 120 hours.

The rules were expected to escalate here; they do not. `tests/e2e/test_decision_quality_rules.py` locks the 18 results, so any change in the rules' decisions is named.

## Step 3: grading Haiku the same way (built; the paid run needs the owner's approval)

- **Builder:** `app.build_haiku_eval_models` builds the paid decider in the composition root, the one place allowed to build a model route. It uses Claude Haiku on the active investigator prompt (`case_investigator_agent` v3) and the real reply reader, behind the spending guard. The route's cap is the run's budget, measured against the run's own call log, so the guard refuses the call that would cross it.
- **The same evidence for every decider (`AtRound`).** For a later-round point the rules decide the earlier rounds and Haiku decides only the graded one. Without this, a Haiku that asked the hub where the rules asked the seller would reach a different round 2, or none.
- **Metering:** each point records its model calls, cost, tokens and latency, read from the run's log. The run records model, prompt, total cost, cap and fallbacks to rules.
- **Budget:** the run stops before a point once less than USD 0.02 of the budget is left. Points not run are listed and never graded. No retries.
- **The script refuses** a paid run without `--yes-paid`, without a cap between 0 and USD 0.50, or without ANTHROPIC_API_KEY.
- **Tested offline:** `tests/e2e/test_decision_quality_model.py` uses a scripted model, never a real one.

**A harness fix found by the full suite:** the scripted reply reader now uses the rules' item list only when the case asked for those items and not the recorded investigator's. The first version also switched when both lists appeared, and broke 14 recorded scenarios.

## The first paid result: rules vs Haiku (one run each, 2026-11-25)

Owner-approved: cap USD 0.10. Spent USD 0.0408 in all, across three attempts.

| | Rules | Haiku (claude-haiku-5-5, v3) |
|---|---|---|
| Acceptable | 17 of 18 | 16 of 18 |
| Not acceptable | 0 | 2 - DQ-10, DQ-17 |
| Prohibited | 1 - DQ-14 (wait) | 0 |
| Cost | 0 | USD 0.0312 (23 calls, 168k tokens in) |
| Latency per point | - | median 6.4 s, max 15.4 s |

**Where they differ:**
- **DQ-14:** Haiku escalated, which is acceptable; the rules wait. On a similar note live, Haiku waited. One run cannot say how often it gets this right; that is what repeat runs are for.
- **DQ-10:** Haiku upheld a parcel 19% larger than declared, calling it "only slightly above"; the owner's key says check first.
- **DQ-17:** after the seller's photo reply, Haiku asked the seller again for dispatch dimensions; the owner's key says hand it to a person or ask the hub.
- **DQ-09 and DQ-12:** both chose differently from the rules (hub first; escalate), and both choices are acceptable.

**What the action grade cannot see** (these flaws are for the judge, step 4):
- Haiku wrote the amount as "₱76.00", a peso sign, not the rupee.
- On DQ-14 it called the hub's comment "an untrusted seller note".
- On DQ-10 it called a 19% difference "slightly above".
- It cites internal fact names (`f_chargeable_grams`).

**The two attempts that failed before the result** (both fixed, both found by real calls):
1. Every replay restarts the fake call ids, and in one shared log the ids collided. A call was made and billed, then its record was refused.
2. Fake case ids also restart, and in one shared log the per-case call limit (6) counted every point as the same case. The guard refused every call after the 20th.

Real ids for the model route and one call log per point fixed both. The run's cap is shared across the logs.

## Step 4: the LLM judge (built; calibration labels and the paid run need the owner)

**The question.** The action grade cannot see a flawed explanation: Haiku's run showed a peso sign, the hub's comment called a seller's note, and 19% called "slightly". The judge (`modules/reasoning/llm/judge.py`, prompt `decision_judge` v1) answers three questions about one explanation, given only the evidence at that decision point:
1. which material claims the evidence does not support (figures with their currency and unit, who said what, what happened, what is proven, what history shows, descriptions that contradict a figure);
2. whether a suspicion is stated as fact;
3. whether the action is reasonable (kept separate, for live cases with no answer key).

Internal ids, wording and omissions are not flaws.

**Pass or fail is code's.** An explanation fails when the judge lists a claim or reports a suspicion stated as fact. The model is never asked for a verdict that could disagree with its own findings, and a bare verdict is refused as invalid output.

**The explanation is untrusted.** It is the agent's text, but it can echo an outside note (DQ-16's quotes a finance approval). It reaches the prompt only as `UntrustedText` with the new origin `agent_rationale`, in a fenced block. The new purpose `decision_judge` puts every call through the spending guard and the call log.
- **Kernel additions:** one origin label and one purpose literal; nothing else in the kernel changes.

**Calibration set** (`evals/decision_quality/judge_calibration_v1.yaml`, draft until the owner labels it):
- **28 explanations:** the 18 real ones from Haiku's run and 10 crafted by Claude (7 deliberately flawed, 3 clean controls).
- **Labels:** pass or fail, the flaws, and whether a flaw is critical, meaning it could lead a reviewer to a wrong money or safety decision.
- **Drafted by Claude, confirmed by the owner:** 15 pass, 13 fail, 7 critical.
- **The judge sees** the decision point's evidence, the action and the explanation, never the label. A test enforces this.

**Measured** (`agreement`): agreement with the owner's labels, misses (a flaw passed, the worse error), false alarms, and critical flaws caught. A judge that passes everything is shown to fail calibration (a test).
- **Proposed bar before the judge is trusted on live decisions:** every critical flaw caught and at least 85% agreement. Both are the owner's to set.

**Model:** the judge runs on any guarded route. Only the Claude route has working credentials today (Foundry has an endpoint but no client credentials), so the first calibration runs on Haiku, the same family as the investigator, which may make it lenient on its own style. Calibration measures exactly that, and moving to gpt-5-mini is the `--judge-route` option.

### The calibration result (owner-approved labels; Haiku as the judge; cap USD 0.05; spent USD 0.034)

**First run (USD 0.021).** 12 of 28 answers were invalid: they were cut at 800 output tokens. One false alarm came from the rubric itself: "any currency but INR or ₹ is wrong" flagged "Rs 500". Both were fixed in the prompt before activation (the meta changelog records them): JSON only, every field under 25 words, a 1500-token limit, and Rs named as rupees. Invalid answers now record their reason, and judge calls stop at a USD 0.003 margin.

**Second run, all 28 again (USD 0.013):**

| | Result |
|---|---|
| Agreement with the owner | 25 of 28 (89%) |
| Critical flaws caught | 7 of 7 |
| Misses (a flaw passed) | 0 |
| False alarms | 3 - R-06, R-09, R-15 |
| Invalid answers | 0 |

**The false alarms are strictness, not invention.** The judge listed true statements as unsupported because the golden `evidence` lines are a short summary of what the agent saw ("recalculated to the same amount", a restated measurement). On flawed explanations it also lists such lines beside the real flaws.

So, before the judge runs on live decisions, it must be given the case's full evidence round, not a summary, or the false-alarm rate will rise. This judge is also Haiku, the investigator's own family; a run on another family (gpt-5-mini) would show whether it is lenient on Haiku's style.

## Step 5: the results are stored, for a dashboard to read

**Where.** Every run is saved to `evals/decision_quality/runs/<run_id>.json`, tracked in the repository next to the golden and calibration sets it was graded against.
- **Why not `data/`:** it is git-ignored and lost when Streamlit Cloud restarts.
- **What committing buys:** the history survives restarts, travels with the code that produced it, and the deployed app can read it with no fetch, no secret and no new service.
- **Not yet:** a separate `eval-results` branch only pays off once a scheduled job writes runs daily.

**`index.json`** is rewritten on every save. It is one row per run, newest first, with:
- kind, date, commit, decider, model, prompt;
- golden and calibration digests, grader version;
- cost;
- the grade counts and median latency, or the judge's agreement, misses, false alarms and critical catches.

**Reading** (`modules/evals/decision_quality.py`):
- `load_runs` and `run_summary` read the records;
- `comparable` names why two runs must not be compared (a different golden set or grader version);
- `changes` lists the decision points that got worse or better. A point not reached is neither, so a run that stopped early is not read as a regression.

**Honest records.**
- **Commits:** a run records its commit with `-dirty` when tracked files differed from it.
- **Prompts:** a run records the prompt's content hash beside its version, because a prompt edited before activation keeps its version name (the judge's v1 was).
- **Corrected:** the three stored runs were made from uncommitted code and are marked `76de563-dirty`.

**Kept:** the Haiku decisions run, the second judge calibration, and a fresh rules run.

**Not kept, because they would mislead a trend:**
- the two failed Haiku attempts (the crash, and the call-limit run that reached 4 of 18 points);
- the first judge run, made on the pre-fix prompt text.

All three are described above.

## Not done

- **Repeat runs** to measure consistency (DQ-14 especially).
- **Activating the judge prompt** (status `inactive` until the owner accepts this calibration).
- **The judge on live decisions,** which needs the full evidence round per decision.
- **The LLM judge (step 4)** and its calibration set of flawed explanations.
- **Storage beyond the local file,** and the dashboard.
- **DQ-14's gap is recorded, not fixed.** A fix to B5, or the hub re-ask that ADR 0062 deferred, is the owner's call.
