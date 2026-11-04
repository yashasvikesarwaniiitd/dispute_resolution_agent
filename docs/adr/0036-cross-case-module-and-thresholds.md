# 0036 — The cross-case module: thresholds, clustering, and investigations through the executor

Date: 2026-11-04
Status: accepted (Phase 7, P7-01 to P7-07, P7-09, P7-12). Built under the owner's "complete the project" instruction; the open points are listed in HANDOFF (D50-D52).
Kernel diff since `phase-6-complete`: empty (`tests/architecture/test_kernel_untouched.py`).

## Decision

`cross_case` is on, with the `sql_context` adapter. A scheduled job (it runs on the runner's tick, hourly) does five things in order: it finds clusters, describes them, opens investigations, applies ops findings, and checks for recurrence. New disputes that match an open pattern are briefed with it. Every pattern is a caution floor. Everything is in `modules/cross_case`, `infra/sqlite/patterns.py`, migration 0002 and `app.py`.

## Thresholds (config/policy.yaml `patterns`, unchanged)

- **A cluster** is at least `min_related_cases` (3) disputes in `window_days` (14) that share a scanner, hub, SKU or seller. Each pattern records the thresholds that produced it.
- **The floor** for a matching pattern is `match_min_verdict`: REQUIRE_APPROVAL, which may be raised to ESCALATE and never set to ALLOW.
- **The model-call limit** per case becomes `llm.max_calls_per_case_with_patterns` (10) when the module is on, because two-pass reasoning costs two calls (ADR 0025). Start-up applies it, and it never lowers a higher limit.

These values come from Stage A (config.md). The synthetic fixtures were written to them. Real tuning needs real data, which this project does not have (I11).

## Clustering

Clustering is code, never a model (`analyzer/clustering.py`). When groupings overlap, there is one explanation per set of cases:

- a cluster whose cases are a strict subset of another cluster's cases is dropped;
- among clusters with identical cases, the narrowest physical cause wins, in the order scanner, hub, SKU, seller.

**Known limit.** A narrow cause hidden inside a much broader group is reported as the broad group. That only widens who is briefed with caution. A case whose shipment keys cannot be read is reported, never guessed.

## Investigations go through the executor, without a case

The executor and the policy context take a case. A pattern is not a case, and the socket offers no case-less action. Two stand-ins keep the kernel unchanged and keep I2 whole:

- **A subject case built in memory.** It carries the pattern id and the newest member case's facts. It is never stored and touches no case row. It lets the real rules run (R02 kill switch, R03 module availability) and makes the idempotency key stable.
- **The module's own ActionStore** (`pattern_actions`). It has the executor's semantics: a claim that counts attempts, status stamps, and look-before-write. Its table refuses every action type except `UPSERT_ISSUE`, and it answers zero or None for credits, approvals and messages.

The kernel's `ActionExecutor` and `PolicyEngine` classes do the work, so the investigation gets the same checks as every case's actions:

- the policy verdict;
- the kill switch, checked inside execute;
- look before writing;
- read-back verification.

The intent (its exact payload) is written once and reused, so a re-run plans nothing once the issue is VERIFIED. A FAILED write is planned again on the next run, under a new key, because a known failure did not happen.

**Two honest limits:**

- `IssuePayload.kind` can only say `remeasurement`. The investigation is marked by its label `cross_case_investigation`, not by its kind.
- The issue text is fixed at first detection; later members are recorded in our own link table. Updating a Jira issue in place is not a port operation.

## Read-only lookups while a case is being written

Every engine here starts its transactions with `BEGIN IMMEDIATE` (ADR 0008). The pattern lookup runs inside the investigate step, while the case's own write transaction is open. On such an engine the lookup would wait for that transaction, time out after 5 seconds, and brief every patterned case "pattern store unavailable". The S15 scenario found this.

- The provider now reads through a second engine with deferred transactions (`create_sqlite_engine(deferred=True)`), which in WAL mode never waits for the writer.
- The runner now hands outcomes to the observer with no transaction open.

## The pattern analyst (P7-04)

- **Called through `ModelGateway`.** It is off unless the model route is on; until then patterns keep their code-written summary.
- **Checked in code before anything is stored.** An answer is refused when:
  - it has the wrong shape;
  - a hypothesis names an identifier as its subject (PA-E1);
  - a hypothesis lacks independent confirming evidence (PA-E2);
  - it accuses anyone, or mentions credits or refunds (the manipulation case);
  - it cites a reference or a number the statistics did not give.
- **No circular support (PA-E3).** The statistics it sees leave out cases that could have been shown the pattern.
- **Prompt fix.** A pre-release edit made the two examples in the prompt match its own schema; as written, no answer copied from them could validate.
- **Status: `inactive`** until a live eval run passes (prompts/README). That run is paid and needs the owner's approval.

## Consequences

- Patterns can only add caution. The proof is a property test over random inputs (`test_caution_only.py`) and scenarios S15 and S16.
- The module is optional. With it off, the same disputes end the same way (`tests/e2e/test_cross_case_optional.py`), and `make eval` runs on a line with no patterns at all.
- A Phase 5 defect surfaced on the way: the outcome outbox could not read back what it stored. The first closed case made every runner tick raise. It is now fixed and tested.
