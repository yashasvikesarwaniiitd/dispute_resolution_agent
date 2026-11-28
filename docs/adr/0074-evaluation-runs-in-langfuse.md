# 0074 — Evaluation runs in Langfuse

Date: 2026-11-27
Status: **accepted** (owner, 2026-11-27: import paid run 3 into Langfuse, and capture every future evaluation run). Builds on ADR 0057 (AI calls traced to Langfuse) and ADR 0072/0073 (the investigation evaluation).

## The problem

The three paid evaluation runs were JSON files and markdown reports. Nobody could search a case, compare it across architectures, walk an investigation step by step, or label why it failed.

## Decision

- **One trace per execution.** An execution is one case on one architecture.
  - The root observation holds the case under test as input and the final decision as output.
  - Under it, in order: every model call (a generation, with tokens, cost and latency from the spending guard's call log), every tool call (arguments, result, whether it answered, whether the decision cited it, whether code or the model chose it), every code check of a decision (accepted, or rejected with its exact reason), and every answer that failed the output schema.
- **Finding and comparing.**
  - **Session:** `<run>:<case>`, so a case's three architectures group together.
  - **Tags:** `case:IC-15`, `arm:enriched`, `outcome:not_acceptable`, `incorrect`, `unnecessary-seller-request`, `decision-rejected`, `format-failure` and `uncited-tool-calls`.
  - **Scores:** outcome, correct, unnecessary hub and seller requests, rejected decisions, format failures, uncited model tool calls, cost, the most expensive call, latency, and the grader's other measures.
  - **Experiments:** one dataset item per case (`investigation-eval-cases-v1`) and one experiment per architecture, so Langfuse's experiment view compares a case side by side.
- **Manual annotation.**
  - **Queue:** "Investigation evaluation: failures to review" holds every execution that was incorrect, made an unnecessary contact, or had a rejected decision or a format failure.
  - **Score configs:** `failure_category` (categorical) and `failure_notes` (text).
- **What a run did not record is labelled "unavailable".** For run 3 that is:
  - the prompts and raw replies (the final decision is recorded);
  - the content of rejected decision drafts (their reasons are recorded);
  - the v3 loop's tool arguments and results (it records step names only);
  - tool results beyond 400 characters, and tool durations.

  Timing is rebuilt from the recorded durations, because the replay clock is simulated; each trace says so.
- **Content is allowed in evaluation traces only.** Evaluation runs replay synthetic data in a fake world, so their traces carry prompts, replies and tool results; that is the point of reviewing them. Production tracing (ADR 0057) is unchanged and still sends no content (I14). The evaluation path sets `environment = evaluation`.
- **Idempotent.**
  - Every id and timestamp is derived from the record.
  - Scores and dataset items are upserted by id.
  - Queue items and score configs are reused when they already exist.
  - A local manifest (`evals/investigation/runs/langfuse_manifest.json`) stops a trace from ever being sent twice. A trace whose content changed is reported instead.
  - **Why (observed on Langfuse Cloud, 2026-11-27):** a second import re-sent 20 traces with the same ids and start times. For about 30 minutes they showed twice (270 observations instead of 152); Langfuse then merged them into the last version. No deletion was needed.
  - **To replace a trace on purpose:** `--replace <trace ids>` deletes it in Langfuse and sends it once under a new id, never the id being deleted. Use it only with the owner's approval, because a deletion can't be undone.
- **Transport:**
  - **Traces:** OpenTelemetry, through the existing endpoint helper and exporter. Langfuse Cloud removed its legacy ingestion and dataset-run-item endpoints on 2026-11-16.
  - **Everything else:** the public REST API (scores, score configs, datasets and annotation queues).
- **Future runs are captured automatically.**
  - `scripts/investigation_eval_matched.py` (the runner behind runs 1–3, now in the repo) and `scripts/investigation_eval.py` import each paid run when `LANGFUSE_*` keys are set. Use `--no-langfuse` to skip.
  - The matched runner now also keeps every prompt and reply, every turn field, and tool results up to 1,500 characters.

## Addendum (owner, 2026-11-28): plain language, and run 3's first prompts rebuilt

- **What a reviewer reads is plain English.**
  - Traces: "Case IC-20 · investigator, picks its own look-ups".
  - Steps, numbered in order: "Step 2: Check how this scanner has been measuring", "Step 5: the agent makes its decision", "Step 6: Rule check of the decision: rejected".
  - Boxes: "What the agent was shown", "Decision: Ask the seller for evidence", "Why", "What it found", "Graded: Correct".
  - The test description is labelled as never shown to the agent.
  - "What the agent was shown" is sorted into numbered categories. When present, they are:
    1. the dispute;
    2. what the seller wrote (not verified);
    3. the bill, as checked by code (charged, should be, overcharge, declared and measured size);
    4. evidence on file (found and missing);
    5. contact so far;
    6. automatic screening;
    7. related cases;
    8. investigation so far;
    9. what the agent may decide;
    10. look-ups the agent may use;
    11. limits;
    12. what it was asked to do;
    13. ids it may point to.

    A prompt it does not recognise is shown as it is. The exact text stays in the first step ("Exact text the agent was shown").
  - Tags, score names and metadata keys are unchanged, because filters use them.
- **Run 3's first prompts are rebuilt, with no model call.**
  - The data is synthetic and the replay is deterministic, so `scripts/rebuild_eval_prompts.py` replays each execution with a stand-in for the model and keeps the first prompt. It writes `<record>.inputs.json`; the record itself is not changed.
  - **Checked:**
    - All 30 instructions match the fingerprints in the run's call log.
    - The prompt size tracks the tokens the run was billed for, within about 1% per architecture.
    - The only code change since the run is AUD-05, which does nothing without a seller reply.
  - The importer uses a rebuilt prompt only when it matched, and labels it "Rebuilt after the run".
  - Later prompts depended on the model's replies, which were not saved, so they stay "Not saved by this run".
- **Look-ups read plainly.** Each tool step shows:
  - what it was asked, in plain labels;
  - a one-line "In short";
  - the details (records one per line, scanner windows as sentences, cautions);
  - whether the decision used it.

  Every value stays. Only labels and layout change.
- **Run 3's cut-off tool answers were recovered in full.** The rebuild replays the run's own look-ups on the same synthetic data. All 38 answers start with exactly the 400 characters the run kept. Where the run had cut an answer, the step says it was recovered.
- Run 3's 30 traces were replaced (`--replace`, owner-approved) so Langfuse shows this version.

## Using it

- **Search a case:** the trace name or the tag `case:IC-15`.
- **Compare architectures:** Datasets, then `investigation-eval-cases-v1`, then the experiments; or open the session `<run>:IC-15`.
- **Filter failures:** the tag `incorrect`, or the score `correct = 0`. For contacts, use the tags `unnecessary-hub-request` and `unnecessary-seller-request`.
- **Unnecessary or expensive steps:** the score `uncited_model_tool_calls`, or the tool observations with metadata `cited = false`. Sort generations by cost.
- **Annotate:** Annotation queues, "Investigation evaluation: failures to review".

## Consequences

- **Runs 1 and 2 stay local only**, as the owner asked. The importer can send them later with one command.
- **Decision Quality runs (ADR 0064) are not covered yet.** Their records have a different shape.
- **Not verified:** how long Langfuse keeps the data (its retention).
