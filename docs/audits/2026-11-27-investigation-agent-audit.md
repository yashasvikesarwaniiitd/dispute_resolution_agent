# Post-implementation audit: dispute investigation agent

Date: 2026-11-27. Scope: ADR 0071 (five investigation tools), ADR 0072 (bounded investigation agent, generated data, evaluation) and their effect on what existed.

**Method**
- **Read:** the code, prompts, schemas and data.
- **Ran:** the full unpaid test suite; the regression suites in the frozen (approved-only) environment; new audit tests (`tests/audit/test_investigation_audit.py`); timing probes; a browser check of the Desk.
- **Independent review:** one independent reviewer, given only the code, read the new layer for defects.
- **Not done:** no application code changed, no approved answer changed, no paid model call.

**Labels used throughout**
- **Verified**: I or the reviewer checked it in code or by running it.
- **Assumption**: stated, not checked.
- **Recommendation**: advice.

---

> **Update, same day: corrections made (ADR 0073).** Fixed: AUD-01, 03, 04, 06, 08, 09, 10, 11,
> 13, 14, 16, 20, 22, 23, 25, and the AUD-12 test gaps; AUD-05 on 2026-11-28. Still open:
> AUD-02 and AUD-07 (mandatory release blockers before any real seller data), AUD-15, 17, 18, 19,
> 21, 24. The findings below are as audited.

## A. Executive summary

**What was built**
- **Code still does the basics.** It gathers the core evidence, does every calculation, proves billing errors and gives the authoritative history comparison.
- **The agent investigates only when that isn't enough.** It chooses among five read-only tools, builds the arguments, reads results, refines, then decides.
- **Supporting work:**
  - 7 generated sellers (449 shipments in total), with a frozen copy of the approved data;
  - an evaluation set of 20 cases, with a scripted harness and a paid-comparison script;
  - prompt caching and per-turn cost logging;
  - seller-page search, pagination and add-shipment.

**Does it match the approved architecture?** Mostly. The core/agent split, the five tools, code-enforced budgets and the frozen data are as approved. But the audit found **defects that affect evaluation validity and privacy**, and some that pre-date this work.

**Ready for real LLM evaluation? Not yet.** Three things must be fixed first, or the results will be wrong or misleading:
1. **AUD-01, the call cap:** the doubled per-case model-call cap is not applied in the running app or the paid eval. A full investigation round (5 tools, a decision and a retry), or a seller-reply round after a heavy first round, gets cut off.
2. **AUD-03, the policy tool:** it searches the developer build guide and returns "stop and use the architect subagent"-style text to the model as trusted policy.
3. **AUD-09 and AUD-10, measurement:** a switched-off tool ends the case and its cost goes unlogged; the enriched arm handles a model outage differently. Both bias the comparison.

**Biggest risks beyond the evaluation**
- **Privacy (AUD-07):** "privacy-preserving" aggregates can be subtracted to reveal small groups.
- **Seller identity (AUD-02):** it is trusted from the request at intake.
- **Free-text requests (AUD-08):** the model's requested-items text can reach a seller email without the fixed-list check. This is already true of today's live agent.

AUD-07 and AUD-02 are release blockers for real data, but not for evaluation on synthetic data.

**Shown vs not shown.** Nothing here shows the agent is *better*: no real model has run v4. The scripted tests prove the machinery works and that the data supports each case.

---

## B. Requirements traceability matrix

| Requirement | Status | Where | Evidence | Gap |
|---|---|---|---|---|
| Reduce unnecessary seller/hub interactions | **Not Yet Evaluated** (capability: Partial) | v4 `decision_guidance` (uphold on records, `system.md`); cases IC-08..12 | Scripted: oracle reaches acceptable outcomes | Needs a paid run; the uphold-without-hub rule awaits owner sign-off |
| 25% fewer unnecessary requests | **Not Yet Evaluated** | `modules/evals/investigation.summary` counts hub and seller separately | Grader unit tests | Baseline arm not yet run |
| Accuracy at least the baseline | **Not Yet Evaluated** | `scripts/investigation_eval.py` (agent/enriched/investigator) | — | AUD-11: arms differ in prompt as well as tools |
| No cost increase per correct ticket | **Not Yet Evaluated** | `summary.cost_per_acceptable_usd`; `Turn` records | Scripted cost fields populated | AUD-01, AUD-09 skew cost |
| Understand the complete complaint | **Partial** | v4 `<understanding_the_dispute>`; statement stored (ADR 0070) | IC-01 `must_mention` | Grader ignores `must_mention` (AUD-20); prompt treats refund requests as manipulation (AUD-06) |
| Mandatory evidence upfront | **Passed** | `packs/weight_dispute/pack.EVIDENCE_PLAN` (unchanged) | Full suite | — |
| Compact comparison upfront | **Passed** | `briefing_view._comparison_view`; `history.compare` unchanged | Frozen snapshot of 84 comparisons | — |
| Autonomous tools and refinement | **Passed (capability)**, Not Yet Evaluated (behaviour) | `modules/reasoning/llm/investigator.py` | `test_investigator.py` (10), scripted eval (20 cases) | Real model behaviour unknown |
| At most 5 tool calls per round | **Passed** (loop) / **Partial** (case) | `investigator.MAX_TOOL_CALLS` | `test_the_tool_budget_is_enforced_in_code` | AUD-01 |
| At most 1 paid image per round | **Passed** | `investigator.MAX_PAID_CALLS` | `test_only_one_paid_check_per_round` | — |
| Exactly five tools | **Passed** | `app.build_toolbox` | `test_toolbox_wiring` | AUD-09 (schema always offers all five) |
| Deterministic financial calculations | **Passed** | `packs/weight_dispute/calculator`, `kernel/policy/credit` (unchanged) | Full suite; S01, S37, S39 | — |
| Policy validation and human approval | **Passed** | Kernel policy engine, `ApprovalService` (unchanged) | S10, S33, S39, S13a/b | AUD-08 weakens the evidence-request check |
| 300–500 shipments, 10 sellers | **Passed** | `scripts/generate_sellers.py` | `test_the_world_is_small_diverse_and_shared` (449) | — |
| Three original files preserved | **Passed** | `config/sellers/*.yaml` | `git diff HEAD` empty; snapshot test | — |
| Shared hubs and scanners | **Passed** | Generator `BLR/DEL/BOM/HYD/MAA` | Same test (up to 4 sellers per scanner) | — |
| Frozen regression environment | **Passed** | `catalogue.MODE`, `SELLER_CATALOGUE` | Frozen run (section D) | Scanner stats in frozen mode are all `insufficient_data` (one seller per scanner): reproducible but uninformative |
| SQLite, ready for PostgreSQL | **Partial** | `records.ShipmentRecords`, `sql_context.tools.CaseOutcomes` | — | Section C3 |
| Baseline needs at least 5 trusted | **Passed** | `history.MIN_REFERENCES`; tool `_stats` | Audit: fewer than 5 gives sample size only | — |
| Cross-seller aggregates only | **Failed** (property) | `tools._aggregate`, `PastDisputesTool` | `xfail AUD-07` | Differencing (AUD-07) |

---

## C. Architecture and modularity

### C1. Current architecture

```
 Seller / Desk UI (web/src)          Reviewer UI (Desk, Streamlit console)
          |                                   |
     ui/web/router.py  ----------------  api/ (events, runtime, runner)
          |                                   |
          |                     kernel/orchestrator  (state machine, steps, policy, executor)
          |                       |        |            |
          |               investigate.py   policy/   executor + write ports
          |                       |
          |               Reasoner port  ---->  modules/reasoning/llm/investigator.py
          |                                         |            |
          |                              ModelGateway port   Toolbox (kernel/orchestrator/toolbox.py)
          |                                   |                  |  InvestigationTool port
          |                  modules/model_gateway/*      +------+------+------+------+
          |                                               |      |      |      |      |
          |                                   evidence/  evidence/ cross_case/ image_check/ reasoning/
          |                                   past_ship  scanner   past_disp   parcel_img   policy
          |                                        \      /           |
          |                                    ShipmentRecords     CaseOutcomes
          |                                    (records.py)        (repository.py)
          +---- reads catalogue directly (ui/seller.py) ---- catalogue.py (YAML) / added.py (SQLite)
```

### C2. Separation of concerns (verified)

**Clean**
- **Kernel:** state, policy, money and execution don't depend on modules (import-linter "Kernel is pure", "Modules are independent"; arch tests pass).
- **Tools:** each lives in the module that owns its data, behind one port. The Toolbox handles registration and error capture; tools validate their own arguments with pydantic.
- **Briefing:** built in one place (`briefing_view.build_view`).
- **Money:** `pack.assess` and `kernel/policy/credit`, untouched.
- **Evaluation:** the grader (`modules/evals/investigation.py`) and the replay (`api/investigation_eval.py`) are kept apart; the answer key is fenced by an architecture test.

**Mixed**
- **`app.py` keeps growing (about 1,700 lines).** It is the only allowed composition root, so it legitimately builds everything. But the new eval builders (`eval_toolbox`, `build_investigation_models`) sit beside production wiring.
  - Impact: the cap bug (AUD-01) happened because three builders apply `engine_policy` differently.
  - Recommendation: one `gateway_policy(engine)` function used by every builder. Not a file split.
- **`ui/web/router.py` holds catalogue logic.** `add_options` and `_kind` read the seller catalogue directly in the API layer. Changing the store means touching the UI layer too (see C3).
- **`ui/seller.seller_shipments`** builds UI rows, reads the added-shipment store and computes comparisons on every request (AUD-15).

### C3. Database migration (SQLite to PostgreSQL/Supabase)

| Question | Answer |
|---|---|
| Swap the adapter without touching prompts or reasoning? | **Yes for the agent loop and prompts:** they see only the Toolbox and the briefing. |
| Are DB queries isolated? | **Partly.** Tool reads go through `ShipmentRecords`/`CaseOutcomes`; the case store is behind kernel ports (SQLAlchemy Core). But shipment history isn't in a database at all: it is YAML (`catalogue.py`) plus SQLite (`added.py`). |
| Transactions and integrity | Case state uses unit-of-work transactions and optimistic versioning (existing contract tests). Added shipments use a separate SQLite file with no shared transaction with the ledger seed (`ui/add_shipment.save` writes the store, then the ledger). A crash between the two leaves a shipment without its debit. Low risk for the demo; P2 for production. |
| Seller restrictions outside the model? | **Yes for history records:** `_this` plus `seller_records` filter by the case's seller in code. **No for identity:** AUD-02. |
| SQLite-specific business logic? | None found in tools or the loop. The added store uses raw `sqlite3` (adapter-local). |
| Must change | `records.CatalogueRecords` (new implementation), `added.AddedShipments`, `sql_context/repository` (SQL dialect), `ui/seller.py`, `ui/web/router.add_options`, `catalogue.py` loading. |
| Unchanged | `investigator.py`, prompts, `Toolbox`, tool classes (they take the interface), kernel. |

### C4. Model independence

| Ability | Verdict |
|---|---|
| Swap Claude Haiku for another model | **Yes.** Every call goes through the `ModelGateway` port; routes exist for Anthropic, Foundry OpenAI and fake models. |
| Separate image model | **Yes.** `analyze_parcel_image` takes any `ImageChecker`. But the eval toolbox has no vision route on the Anthropic path (IC-07 can't be tested with a real model there). |
| Scripted models in tests | **Yes** (`FakeModelGateway`, scenario `ScriptedModel`). |
| Same dataset across engines | **Yes:** `run(models_for, ...)` takes any factory. |
| Prompts independent of orchestration | **Yes:** versioned prompt files; the loop reads name and version. |
| Budgets and timeouts by configuration | **Partial.** The call cap and spend caps are in `policy.yaml`. The loop's `MAX_TOOL_CALLS`, `MAX_PAID_CALLS` and `MAX_RESULT_CHARS` are code constants (reasonable defaults, but not configurable). |
| Provider coupling | Prompt caching is implemented only on the Anthropic route (fine). The 512-token minimum comes from the vendor table and is stated as such. |

### C5. Tool extensibility (by inspection, plus the wiring test as a pattern)

- **Add a sixth tool:**
  - write a class with `spec` and `run`;
  - register it in `app.build_toolbox` (and `eval_toolbox`);
  - add the name to v4 `schema.json`'s `step` enum and to the prompt's `<tools>`.
  - That's three places. The schema enum is the coupling (AUD-09), and listing the tools in prompt text duplicates `ToolSpec.description`.
- **Turn a tool off by configuration:** only per module today (switching a module off drops its tools). There is no per-tool switch. Because the schema still lists the tool, choosing it ends the case (AUD-09).
- **Change a data source:** pass another `ShipmentRecords` or `CaseOutcomes`. No other change.
- **Test one tool alone:** yes; every tool has standalone tests.
- **Add a filter:** a field on `PastShipmentsArgs` plus a line in `_matches`, `_differs` and `_available`. Local.
- **Change a response schema:** local to the tool. The model reads JSON, with nothing downstream parsing it, except the grader's `INFORMATION` mapping by tool name.

### C6. Code quality (significant items only)

- **Duplication:**
  - `_vol`, `_ratio` and the 1.25× line duplicate history concepts. They reuse `history.ANOMALY_FACTOR`, `sorted_sides` and `volume`, so the shared logic is mostly imported. OK.
  - The cap logic is duplicated across three builders (AUD-01). That is the one real duplication bug.
- **Hard-coded values:** loop budgets, the result cap, aggregation thresholds (5 readings / 3 sellers) and fixed windows (60 and 365 days) are constants. All are documented; consider moving the budgets to `policy.yaml`.
- **Dead or unused:**
  - `Case.must_mention` and `Case.v3_expected` are never graded (AUD-20).
  - `ENRICHED_DEFAULTS` includes `search_past_disputes`, which returns nothing in replays (AUD-25).
- **Error handling:** consistent (tools return `ok=False`, the Toolbox never raises), except AUD-09 and AUD-10.
- **Typing:** mypy is clean; pydantic sits at the tool boundaries.
- **Shared mutable state:** `InvestigatorReasoner.trace` and `_memory` are per instance. Safe today (single runner thread), unsafe with concurrent cases (AUD-19).

### C7. Independent testability (without paid calls)

| Component | Testable alone | Evidence |
|---|---|---|
| Each tool | Yes | `tests/unit/tools`, `tests/audit` |
| Historical comparison | Yes | `tests/unit/packs/*history*`, frozen snapshot |
| Statistics | Yes | Audit `test_statistics_match_an_independent_calculation` |
| Financial calculation | Yes | Existing calculator and credit tests |
| Briefing and prompt | Yes | `tests/contract/test_prompt_registry.py`, investigator tests |
| Tool argument validation | Yes | Toolbox `bad_arguments` tests |
| Agent loop with a scripted model | Yes | `test_investigator.py` |
| Database access | Yes | Contract persistence tests |
| Policy engine | Yes | Existing policy tests |
| Executor with mocks | Yes | S12–S13, failure drills |
| Cost tracking | **Partly** | Turn records tested; **`cached_cost` and `_system` have no tests** (AUD-12) |
| Case memory (prior rounds, reviewer notes) | **No test** | AUD-12 |

---

## D. Test report

| Run | Tests | Passed | Failed | Skipped / expected-fail |
|---|---|---|---|---|
| Full unpaid suite (`-m "not integration and not live_model"`), including the audit tests | 4,531 | 4,460 | **0** | 71 skipped = 65 integration/live skips + 6 audit expected failures (97 more deselected by the marker) |
| Frozen environment (approved sellers only): scenario suite, Decision Quality rules replay, Phase 7 scenarios, approved-catalogue tests | 82 | 82 | **0** | — |
| (Audit harness error, not a defect) the 3 full-world tests in `test_generated_sellers.py` were also run in frozen mode, where they correctly fail ("3 sellers, expected 10") | 3 | 0 | 3 | expected: they assert the full world |
| New audit tests (`tests/audit`) | 18 | 12 | 0 | 6 xfail (AUD-01, 03, 04, 05, 06, 07) |
| `make verify` (lint, types, architecture, tests) before the audit | 4,447 | 4,447 | 0 | 65 skipped, 97 deselected (integration/live) |

**Regression differences caused by the expanded data (verified)**
- **Approved comparisons:** none change (snapshot).
- **Scanner statistics:** for approved disputes they differ between full mode (shared scanners) and frozen mode (`insufficient_data`). Expected and documented.
- **Seller switcher:** shows 10 sellers (UI tests pinned to frozen mode).
- **Ledger:** seeds 449 debits at start-up instead of 84 (no test depends on the count).
- **Start-up:** the catalogue loads in 1.25 s.

**Failures:** none in the existing suites. The 6 audit expected-failures are defects; reproduction steps are in section G.

**Coverage of the audit's T01–T50**

| Status | Count | Scenarios |
|---|---|---|
| **Covered** by existing or new tests | 39 | T01–T05, T07–T17, T19–T21, T26–T35, T37, T40–T43, T45–T48, T50 |
| **Partial** | 8 | T06 (`must_mention` ungraded), T25 (reviewer notes now in the briefing, untested), T36 (screen OK, prompt conflict AUD-06), T38 (tools isolate, intake doesn't, AUD-02), T39 (thresholds yes, differencing AUD-07), T44 (enriched outage path AUD-10), T49 (browser-verified, no automated test), T22 (reply reader handles partial, no investigator case) |
| **Gap** | 3 | T18 (adaptive re-investigation needs a real model), T23 (seller-supplied photos can't be analysed: the tool reads only hub photos), T24 (no cross-round contradiction case or test) |

Mapping detail is in the appendix.

---

## E. Agentic capability

| Capability | Implemented? | Shown with a real model? |
|---|---|---|
| Decide whether to investigate (zero tools) | Yes: a decide turn first is accepted | No |
| Choose any available tool | Yes: step from the schema enum, dispatched by name | No |
| Build arguments | Yes: an `arguments` object, validated per tool | No |
| Observe results | Yes: notes T1..Tn on the next turn | No |
| Refine with different arguments | Yes: a different-argument call runs; the same arguments are refused | No |
| Choose the next tool based on results | Yes structurally: the model sees all earlier results | No |
| Stop when enough is known | Yes: decide at any turn; forced at budget | No |
| Exactly one permitted action | Yes: `check()` holds it to the menu, plus policy | Scripted |
| Routes hard-coded in the prompt | No: principles only, verified in `system.md` | — |
| Full history not injected | Verified: the briefing has only the compact comparison; records come via the tool | — |
| Cannot bypass safeguards | Verified for amounts, menu, approvals. **Exception: AUD-08** (free-text requested items) | — |

**Scripted trace (IC-09, packaging change; from `tests/evals/investigation`)**

| Step | What happens | Result |
|---|---|---|
| Turn 1 | `search_past_shipments{packaging_id:any}` | T1: the seller ships in tube and box-mat |
| Turn 2 | `search_past_shipments{packaging_id:PKG-PS-BOX-MAT, date_from:2026-09-01}` | T2: supplementary, at least 5 trusted, parcel ≈ 1× typical |
| Turn 3 | decide `uphold_charge` | Passes policy; awaits the person's approval |

The oracle chose this path. It shows the machinery can do it, **not** that the model will.

**Needs real-model evaluation:** whether the model calls tools at all (earlier engines rarely did), whether it refines sensibly, whether it over-investigates, and whether it handles refunds correctly (AUD-06).

---

## F. Cost assessment

**Measured so far (earlier, not this work)**
- **Haiku decision-quality run:** 23 calls cost USD 0.031, about USD 0.00135 per call, on prompt v3 without caching.
- **ADR 0042 agent check:** gpt-5-mini, USD 0.133.
- **This work:** no paid calls.

**Measured sizes (this work)**
- **System prompt:** v4 is 14.5k characters, about 3.6k tokens, and cacheable.
- **Per call:** about 22.5k characters, about 5.6k tokens.
- **Tool results:** a past-shipments search at limit 20 is about 7.6k characters, but the loop cuts results at 3,000 (AUD-14).

**Estimate for 50,000 tickets/day (assumptions; not measured)**

*Assumptions*
- **Price:** Haiku 5.5 at USD 0.10 per million input and USD 0.50 per million output tokens (vendor table); cache reads at 0.1×, writes at 1.25×.
- **Rounds:** 1.6 rounds per ticket.
- **Cost per call:** about USD 0.0011–0.0014, including adaptive-thinking output.
- **Vision:** excluded; it would be an extra paid route.

| Investigation usage | Model calls per ticket | Per ticket | Per day | Per month |
|---|---|---|---|---|
| Today's workflow (1 call per round) | ≈1.6 | ≈ USD 0.002 | ≈ USD 100 | ≈ USD 3k |
| Tools in 20% of rounds, 2 per round | ≈2.2 | ≈ USD 0.003 | ≈ USD 150 | ≈ USD 4.5k |
| Tools in 50% of rounds, 2.5 per round | ≈3.6 | ≈ USD 0.005 | ≈ USD 250 | ≈ USD 7.5k |
| Worst case: every round, 5 tools | ≈9.6 | ≈ USD 0.013 | ≈ USD 670 | ≈ USD 20k |

**What could offset the extra cost:** if avoided hub requests remove a later round, the number of rounds per ticket falls. **Not shown yet.**

**Inefficiencies found**
- **AUD-16:** the case briefing (about 2k tokens) is re-sent uncached on every tool turn; only the system prompt is cached.
- **AUD-14:** past-shipment results are cut at 3,000 characters even though `limit` allows 20 records.
- **No batching:** each tool needs its own model turn, so independent look-ups can't be batched.

**Proposed paid evaluation**
- **Size:** 3 arms × 20 cases, about 90–110 calls per pass, about USD 0.12–0.15 per pass.
- **Recommended:** 3 passes, **about USD 0.45**.
- **Caps:** USD 0.50 per arm-run.
- **Order:** run only after the pre-evaluation fixes.

---

## G. Issue register

**P0: release blockers for real seller data. They don't block synthetic evaluation.**

| ID | Issue | Where | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| AUD-07 | Cross-seller aggregates can be subtracted to reveal a group below the threshold: hub minus scanner, or own seller's records minus a group, or sliding the anchor date via added shipments | `evidence/.../tools.py` `_aggregate` (about l.339–350); `cross_case/.../tools.py` | xfail test; reviewer found 90 of 898 windows where a hidden group is recoverable | A competitor's dispute and scanner record can be inferred | Suppress the complementary group too; count sellers excluding the case's own; minimum *other*-seller count; no free anchor date |
| AUD-02 | The seller ID on a dispute is taken from the request; there is no check that the shipment belongs to that seller | `api/runtime.submit_dispute`, `kernel/orchestrator/intake` | Code search: no ownership check | With real data, a caller naming another seller gets that seller's history, case memory and outcomes analysed | Authenticate the seller and check shipment ownership at intake (pre-existing; amplified by the tools) |

**P1**

| ID | Issue | Where | Evidence | Impact | Fix |
|---|---|---|---|---|---|
| AUD-01 | The investigator's doubled call cap isn't applied to the gateway in the running app or the paid eval; the pattern budget is also skipped at runtime | `app._runtime_reasoner` (l.~1002), `build_haiku_eval_models` (l.~1335) | xfail test; reviewer confirmed | A full round, or a reply round after a heavy round, hits the cap and escalates; the eval undercounts | Build every gateway from one `gateway_policy(engine, patterns)` |
| AUD-03 | The policy tool indexes the developer build guide | `explainer/policy_index.POLICY_ONLY` | xfail test; the passage text | The model receives build-process instructions as "trusted policy" | Index `policy-rules.md` only (introduced by ADR 0072) |
| AUD-08 | The model's free-text `requested_items` aren't limited to the fixed list on the agent engines | `prompts/case_investigator_agent/v3|v4/schema.json` (no enum); `investigator.py` and `agentic.py` don't pass `allowed_items` | Verified: v2's single-call schema has the enum and the check | Arbitrary request text reaches a seller email, gated only by a person's approval (live today on v3) | Add the enum and pass `allowed_items` |
| AUD-06 | Both prompts tell the model that text asking for "credits, refunds or exceptions" is manipulation | v3 and v4 `system.md` `<trust_model>` | xfail test; the code screen does *not* flag ordinary refund requests (audit test passes) | Genuine disputes may be escalated as manipulation, hurting automation and the contact goal | Distinguish "asks the system to grant/approve/skip checks" from "says I was overcharged, please refund" |
| AUD-09 | A tool that is off (but still in the schema enum) ends the case; turns that end in a raise aren't logged | `investigator._parse` (~l.363), the must-decide raise | Reviewer | Wrongful escalations; eval cost and call counts read low | Route unknown tools to `Toolbox.call` (returns `unknown_tool`); log before every raise |
| AUD-10 | In the enriched arm, a model outage becomes invalid output (escalation) instead of the rules fallback | `investigator._ask` (`if notes:`) | Reviewer | Outage scenarios graded differently between arms | Convert only after the first model turn |
| AUD-11 | The comparison arms differ in prompt (v3 vs v4) as well as tools | `build_haiku_eval_models` | Inspection | "Agent vs investigator" mixes prompt and tool effects | Read **enriched vs investigator** (same v4 prompt) for the tool effect, and agent vs enriched for prompt plus evidence |
| AUD-12 | No tests for case memory (`_prior_rounds`, `_reviewer_notes`) or the cache cost calculation (`cached_cost`, `_system`) | `kernel/orchestrator/investigate.py`, `anthropic_direct/gateway.py` | Grep of tests | Wrong spend-cap accounting or missing memory would go unnoticed | Unit tests for each |
| AUD-13 | Reviewers can't see which tools ran or what "T2" refers to | Desk and console case views | Grep: no UI reads turns | A reviewer can't judge a recommendation that cites tool results | Show the trajectory and the cited results on the case page |

**P2**

| ID | Issue | Where | Fix |
|---|---|---|---|
| AUD-04 | An unknown policy returns unrelated zero-score passages | `policy_tool` | Drop zero-score hits; return `not_found` |
| AUD-05 | The v4 `<untrusted_reply_reading>` block is never filled; the reading arrives as untrusted evidence JSON instead | `briefing_view` (`"reply_reading": None`) | Fill it, or remove the block and say where the reading is |
| AUD-14 | Result cap of 3,000 characters vs `limit` up to 20 records (about 7.6k) | `investigator.MAX_RESULT_CHARS`, `PastShipmentsArgs.limit` | Lower `limit` to 8, or compact the records |
| AUD-15 | Full scans and N+1 reads per request (seller page, past disputes `keys_for`, scanner statistics `all_history`) | `ui/seller.seller_shipments`, `library._source`, `records` | Cache misses; read once per request; indexed queries after a database move |
| AUD-16 | The case briefing is re-sent uncached each tool turn | `anthropic_direct._system`, single user message | Split the user message: briefing block with a cache marker, then notes |
| AUD-17 | Cache tokens are only in the JSONL turn log, not in `model_calls`; case-level cost needs a join | `infra/sqlite/model_call_log` | Add columns (with a migration) or a per-case summary function |
| AUD-18 | No per-tool timeout | `Toolbox.call` | A deadline per call, mostly for future network-backed tools |
| AUD-19 | The reasoner's shared `trace` and `_memory` aren't safe for concurrent cases | `InvestigatorReasoner` | Per-call state, or a lock, before concurrent runners |
| AUD-20 | The grader ignores `must_mention`; there is no grounding check; the eval set has no privacy or budget cases (unit tests cover those) | `modules/evals/investigation.grade` | Add a mention check (or the LLM judge) and 2–3 safety/budget cases |
| AUD-21 | Added shipment and ledger debit are written without a shared transaction | `ui/add_shipment.save` | Write the ledger first, or make it idempotent and re-seed on read |

**P3**

| ID | Issue | Fix |
|---|---|---|
| AUD-22 | Earlier rounds' refs ("T2") are ambiguous in a later round; "not run" notes are citable | Drop T*/R* refs from `PriorRound`; cite only successful results |
| AUD-23 | The scripted eval cap can multiply when `REASONING_ENGINE` is set | Pass the engine name explicitly |
| AUD-24 | The image memory key ignores a replaced photo | Key by photo hash |
| AUD-25 | In replays past disputes always returns nothing (fresh world) and IC-07 has no vision on the Anthropic route; both limit what the paid eval can show | Seed past cases in the replay; run IC-07 on the vision route separately |

---

## H. Readiness verdicts

| Area | Verdict | Why |
|---|---|---|
| Functional | **Ready with conditions** | Works end to end in scripted runs and the browser; AUD-01, 03, 09 must be fixed |
| Modularity and maintainability | **Ready with conditions** | Good port and module boundaries; builder duplication (AUD-01) and schema/tool coupling (AUD-09) to tidy |
| Security and financial safety | **Not Ready** for real data; **Ready** for synthetic evaluation | Amounts and approvals hold. Privacy differencing (AUD-07), identity at intake (AUD-02) and free-text requests (AUD-08) must be fixed before real sellers |
| Regression | **Ready** | Full suite and frozen suite pass; approved data and comparisons unchanged |
| Evaluation | **Ready with conditions** | Harness, cases, arms and script exist; fix AUD-01, 03, 09, 10 and read results per AUD-11 |
| Production scalability (50k/day) | **Not Ready** | SQLite single writer, one runner thread, in-process caches, full scans (AUD-15), YAML history, no queue. Acceptable for a prototype |

---

## I. Recommended next steps (smallest safe set)

**Before the paid evaluation.** These are small, reversible and need no product decision:
1. AUD-01: one cap function for every gateway.
2. AUD-03: index `policy-rules.md` only. Also AUD-04: drop zero-score passages.
3. AUD-09: unknown or disabled tool returns `unknown_tool`; log before raising.
4. AUD-10: outage before the first model turn keeps the rules fallback.
5. AUD-14: lower `limit` to 8 records.
6. AUD-12: tests for case memory and cache cost.

**Decisions for you**
- **A. AUD-06, refund wording:** approve changing "credits, refunds or exceptions" in v4 (and v3, which is live) so an ordinary refund request isn't manipulation. This changes live behaviour on v3.
- **B. Uphold-without-hub rule:** still pending from before.
- **C. Paid evaluation:** about USD 0.45 for 3 passes, after the fixes.

**Before real sellers (P0/P1)**
- AUD-07 privacy differencing;
- AUD-02 identity at intake;
- AUD-08 fixed list for requested items;
- AUD-13 tool trace visible to reviewers.

**Later:** the P2 efficiency items, and the scalability work in section H.

---

## Appendix: T01–T50 mapping

| Status | Scenarios and their evidence |
|---|---|
| **Covered** | T01 S37, S65, IC-02 · T02 S01, IC-01 · T03 calculator unit tests · T04 S17, S32, IC-05 · T05 S38 · T07 IC-04, S67 · T08 S62, IC-07, IC-16 · T09 S66, IC-18, IC-20 · T10 IC-09 · T11 IC-08, IC-12, S64 · T12 IC-11 · T13 IC-10 · T14 IC-06, IC-14, S60 · T15 IC-13, IC-20 · T16 audit filters plus `compare` excludes non-trusted · T17 IC-12 · T19 IC-17 · T20 S03, S63 · T21 S31, IC-19 · T26 audit empty-result test · T27 Toolbox `tool_failed` · T28–T29 investigator tests · T30 IC-01–04 · T31 S08 · T32 S44 · T33 S09a, S26, S43a–d · T34 S24 · T35 S14a/b, S40, IC-03 · T37 image observations untrusted (audit) · T40 menu check · T41 R16 · T42 rejected decision test · T43 gateway retry plus parse · T45 S10, S33 · T46 S12a, S13a/b · T47 S11 · T48 persistence contract tests · T50 frozen snapshot plus frozen suite |
| **Partial** | T06, T22, T25, T36, T38, T39, T44, T49 (reasons in section D) |
| **Gap** | T18, T23, T24 |
