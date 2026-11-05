# Progress

## Status

| Stage or phase | Status | Approved by owner | Notes |
|----------------|--------|-------------------|-------|
| A1 Kernel specification | Approved | Owner, 2026-11-02 (D27) | `docs/spec/` (7 files), ADRs 0004-0013; table checks C1-C16 and 27 schema checks pass; review findings fixed; product decisions D19-D26 recorded |
| A2 Safety and policy | Approved | Owner, 2026-11-02 (D34) | 5 spec files, ADRs 0015-0018; checkpoint D28-D31 approved |
| A3 Integrations and Foundry | Approved | Owner, 2026-11-02 (D37) | 4 spec files, ADRs 0019-0023, FOUNDRY_SETUP.md corrected; docs read 2026-11-02 |
| A4 Agent and evals | Approved | Owner, 2026-11-03 (D44) | 6 spec files, 4 prompts revised, ADRs 0024-0025; checkpoint D38-D41; independent review run 2026-11-03, 9 blocking + 22 major findings fixed (D42, D43); 59 scenarios, paths machine-checked |
| A5 Build plan and freeze (`arch-v1`) | Approved; tagged | Owner, 2026-11-03 (D45) | 109 task cards across 9 phases, INDEX, escalation guide, risk register and milestone plan; all 9 phase prompts reference their cards; `docs/spec/` frozen at `arch-v1` |
| 0 Foundations and sockets | Approved | Owner, 2026-11-04 (D54) | 12 cards; ADRs 0001-0003; `make verify` green (767 passed, 114 contract skips); 3 import-linter contracts kept; `/healthz` lists all 11 modules. Phase 0 also built `kernel-interfaces.md` section 4, which no task card covers (see the note below) |
| 1 Kernel | Approved | Owner, 2026-11-04 (D54) | 14 cards; ADR 0026 and implementation notes on 0004, 0005, 0007, 0008; `make verify` green (1204 passed, 129 skipped); a synthetic dispute runs end to end over HTTP and reaches a person with a reason |
| 2 Weight-dispute pack | Approved | Owner, 2026-11-04 (D54) | 11 cards; ADR 0027; pack, calculator, synthetic fixtures, evidence gathering, rules-only baseline (session log 2026-11-03) |
| 3 Real channels | Approved; Jira live (D58), Outlook waits on the owner's directory | Owner, 2026-11-04 (D54) | 11 cards; ADR 0028; Outlook and Jira adapters built and contract-tested against fakes; the live integration run waits on the owner's test mailbox and Jira project (`.env` Graph/Jira settings are empty) |
| 4P Prototype agent (ADR 0029) | Approved | Owner, 2026-11-04 (D54) | `make verify` green (2318 passed); safety review 1 HIGH + 4 MED + 5 LOW, fixed or recorded; `docs/SETUP_FOUNDRY.md`, `make try-agent` |
| 4+ Agent made trustworthy (ADR 0031) | Approved | Owner, 2026-11-04 (D54) | reply interpreter, drafter, two-pass control, injection screen and corpus by every route, Haiku route and F01 failover, per-route caps, Prompt Shields annotations, allow-listed tracing, spend report, eval harness |
| 4b Foundry integration | Covered by 4P and 4+ (ADR 0029) | Owner, 2026-11-04 (D54) | the Foundry agent route, failover, guardrail annotations, tracing and spend caps were built in 4P and 4+; the gpt-5-mini vs gpt-5.4-mini comparison (O1) is still open |
| 5 Approvals and ledger (ADR 0032) | Approved | Owner, 2026-11-04 (D54) | `make verify` green (2739 passed); safety review 0 HIGH + 4 MED + 4 LOW, all fixed with tests (ADR 0032 section 8); simulated ledger, action store, apply/execute/verify stations, credit flow S01 to RESOLVED_CREDITED, verification, credit review view, templates, offline demo |
| 6 Evaluation and hardening (ADR 0034) | Approved; P6-12 passed 2026-11-04 | Owner, 2026-11-04 (D54) | `make eval`: 57 of 57 scenarios, all 12 recorded gates; drills (kill switch, restart, failures); security review maps T01-T22 to real tests; live red team 0 of 24 got through; thresholds approved (D47) |
| 7 Cross-case intelligence (ADRs 0035-0037) | Approved | Owner, 2026-11-04 (D54) | `cross_case` on (`sql_context`); kernel diff since `phase-6-complete` empty (automated); S15 and S16 pass on the running system; caution-only property test; the same disputes end the same way with the module on and off; `make eval` 57 of 57 and all 12 gates; `pattern_analyst` checked by code and kept `inactive` until a paid live run |

## ADRs

Add one line per ADR: number, title, status.

| ADR | Title | Status |
|-----|-------|--------|
| 0001 | Ports and adapters, with a pure kernel | accepted |
| 0002 | Module registry, and what happens when a module is not there | accepted |
| 0003 | Toolchain: uv, ruff, mypy --strict, import-linter, and one `make` | accepted |
| 0004 | Step-driven orchestrator, one committed transition per step | accepted |
| 0005 | State machine as data, with an event acceptance matrix | accepted |
| 0006 | Transactional inbox for inputs, outbox for outcomes | accepted |
| 0007 | Idempotency keys include the action sequence; every write port supports lookup by key | accepted |
| 0008 | Optimistic versions, BEGIN IMMEDIATE, an action claim, one worker process | accepted |
| 0009 | A free-text withdrawal needs human confirmation | accepted (D20) |
| 0010 | Read-back verification with a bounded retry window; "sent" is not "delivered" | accepted |
| 0011 | Money as integer paise and weight as integer grams in storage | accepted |
| 0012 | Deterministic follow-up actions; "resolved credited" means credited and told | accepted (D21) |
| 0013 | The kernel approvals table is the source of truth; ApprovalService only notifies | accepted |
| 0014 | An unclear seller reply gets one clarification, then a person | accepted (D19) |
| 0015 | Verdicts form an ordered lattice; the strictest wins; model output only raises | accepted (D28) |
| 0016 | Credits only for code-proven reasons; no amount caps, structural limit only | accepted (D29, D30) |
| 0017 | Model-call budgets and failover live in one kernel decorator around ModelGateway | accepted (D34) |
| 0018 | Human events are authenticated; the actor comes from the session | accepted (D32) |
| 0019 | Email is sent as a marked draft, looked up by an extended property, read back by immutable id | accepted (D37) |
| 0020 | Jira idempotency comes from our own action record; labels and a property are markers | accepted (D37) |
| 0021 | Foundry agents are called by pinned version, statelessly, and validated in our code | accepted (D37) |
| 0022 | Poll for email replies and Jira findings; no webhooks in v1 | accepted (D37) |
| 0023 | Email uses a personal Outlook.com account with delegated sign-in and an encrypted token cache | accepted (D36) |
| 0024 | The briefing separates trusted facts from untrusted text; the rules-only baseline is the bar | accepted (D44) |
| 0025 | The pattern anchoring control is a total caution order enforced in code, not a model self-report | accepted (D44) |
| 0026 | T90 from GATHERING_EVIDENCE stays; it is reachable, just not by an evidence failure | accepted |
| 0027 | Weight-pack figures (Phase 2) | accepted |
| 0028 | Phase 3 implementation notes | accepted |
| 0029 | A prototype Foundry agent first, then improve it (with SDK addendum) | accepted (owner, 2026-11-04) |
| 0030 | The model-call log lives in its own database file | accepted |
| 0031 | Phase 4+ decisions: guardrails, structured output, injection layers, number words, failover | accepted |
| 0032 | Phase 5: the ledger, the stations after approval, and how a credit is proven | accepted |
| 0033 | The inbound half: dispatch, transition effects, timers and the reply round | accepted |
| 0034 | Evaluation approach and release gates (Phase 6) | accepted (D47, D48) |
| 0035 | HOOK A connected in the kernel (one disclosed change before Phase 7) | accepted (D49) |
| 0036 | The cross-case module: thresholds, clustering, investigations through the executor | accepted |
| 0037 | The confirmation rule: only independent evidence moves a pattern | accepted |
| 0038 | A Gmail API email adapter, and the first kernel change after Phase 7 | accepted (D59) |
| 0039 | A read-only Case Explainer on the Claude Agent SDK, and a bounded I14 exception | accepted (D60) |
| 0040 | Policy lookup with LlamaIndex BM25 (local, no embeddings) | accepted (D60) |
| 0041 | A LangGraph two-pass investigator, off by default and measured head to head | accepted, experimental (D60) |

## Note from Phase 0: the section 4 domain models have no task card

`kernel-interfaces.md` section 4 — `Case`, `Event`, the evidence, assessment, briefing,
proposal, intent, verdict, approval, action and timer models, and the write-port models,
around forty in all — is named by no card in any of the nine phase files. Card P0-05 cannot
type a single port without them, so Phase 0 built them (commit `63143b5`), split by topic
across `models.py`, `evidence.py`, `reasoning.py`, `actions.py`, `external.py` and
`events.py` with the specification's class names.

What this changes for Phase 1: P1-01 and P1-02 build tables and repositories *for* these
models and should expect them to exist. The one deliberate gap is the event payload unions.
`events-catalog.md` defines them as tables per event type; `InboundPayload` and `EventPayload`
are declared as frozen union roots, and the concrete subclasses attach to them without
changing `events.py`. Cards P1-05 and P1-11 are where they are actually read.

The owner may want a card written for this before Phase 1 planning assumes it was free.


## Note from Phase 1: what runs, and what does not

A dispute can be submitted over HTTP, is deduplicated, opens exactly one case, advances
through the step loop, and reaches `NEEDS_HUMAN_REVIEW` with a reason naming the phase that
will decide it. Every state change is an event carrying its transition id and cause, and
`GET /cases/{id}` returns the diary in order.

**No step decides a dispute yet.** There is no domain pack (Phase 2) and no reasoner
(Phase 4), so `eligibility`, `assess` and `investigate` each escalate and say which card
replaces them. That is the honest Phase 1 outcome; a step that guessed so the demo looked
complete would be the worst thing this system could do.

Three things were found between frozen documents and recorded rather than resolved silently:

1. `kernel-interfaces.md` section 4.9 omits `Timer.gather_round`, which `schema.sql` requires
   and D41 depends on. The model carries it with the reasoning on the field.
2. TC-SM-P2 ("no case with an UNKNOWN action is ever RESOLVED") is not a state-machine
   property — hypothesis disproved it in one run. It is carried by the executor, T90 and
   T37 together, and is now tested in those three places.
3. ADR 0008 asked Phase 1 to read and link four SQLite documentation pages before
   implementing. It was not done; the behaviours were verified empirically by fifty
   constraint tests instead. Recorded as outstanding in `docs/BACKLOG.md`.

## Session log

| Date | Stage or phase | Model plan | `/usage` note | What was done | Next |
|------|----------------|------------|---------------|---------------|------|
| 2026-11-02 | A1 | Opus (Claude Code desktop session, not the stage script) | not recorded | Kernel spec drafted: interfaces, schema, transitions + accept matrix, events, orchestrator, executor, traceability; ADRs 0004-0013; machine checks on the table and schema; independent review | Owner approves A1 (O9), then A2 |
| 2026-11-02 | A1 | Opus | not recorded | Owner made 8 product decisions (D19-D26); clarification follow-up added (ADR 0014, X14, checks C15-C16) | Owner sign-off O9, then A2 |
| 2026-11-02 | A2 | Opus | not recorded | Checkpoint approved (D28-D31); policy rules R01-R25, M01-M06, F01; threat model T01-T21; approvals; money rules; test charter; ADRs 0015-0018; independent review | Owner approves A2, then A3 |
| 2026-11-02 | A3 | Opus + 3 research subagents | not recorded | Official docs researched (Graph, Jira, Foundry, Anthropic); integrations, foundry, config, observability specs; ADRs 0019-0022; FOUNDRY_SETUP.md corrected | Owner approves A3 (O13, O14), then A4 |
| 2026-11-02 | A4 | Opus | not recorded | 39 scenarios with checked state paths (59 after the review); owner decided 4 debatable outcomes (D38-D41, two spec changes); agent design; adversarial prompt review (29 failure modes); draft release gates; synthetic data spec; ADR 0024 | Owner approves A4, then A5 |
| 2026-11-03 | A4 | Opus + 3 independent review subagents | not recorded | Independent review of the A4 draft across spec consistency, prompts (adversarial) and evals/gates; 9 blocking and 22 major findings, all verified against the files and fixed; 20 scenarios added (59 total); all paths machine-checked by scripts/check_scenarios.py; kernel-interfaces amendment section 15; owner decisions D42, D43; ADR 0025 | Owner approves A4, then A5 |
| 2026-11-03 | A5 | Opus | not recorded | 109 task cards for phases 0-7 and 4b, each with files, spec sections, tests-first, acceptance command, size, dependencies and model; `docs/spec/INDEX.md`; escalation guide; risk register and a derived milestone plan; task-card reference added to all 9 phase prompts | Owner approves A5, then tag `arch-v1` and start Phase 0 |
| 2026-11-03 | 0 | opusplan (Opus; desktop session) | not recorded | Cards P0-01..P0-12: scaffold and make targets; kernel domain (ids, enums, Money/Weight, canonical hashing, typed errors); cross-case socket models; the section 4 domain models (uncarded, see the note above); 20 kernel ports; Disabled/Null/no-op/fake adapters; module registry with startup validation; composition root, `/healthz` and the API skeleton; structlog with a redaction processor, Clock, IdGenerator, settings; architecture tests, each verified by breaking its rule; 13 contract-test suites; ADRs 0001-0003 | Owner approves Phase 0, then Phase 1 |
| 2026-11-03 | 1 | opusplan (Opus; desktop session) | not recorded | Cards P1-01..P1-14: schema and first migration with 50 constraint tests; repositories with optimistic versioning and a unit of work; the transition table as data (57 rows from 49 declarations); StateMachine.apply and the acceptance matrix; intake and deduplication; policy engine with ten rules and the caution lattice; ActionExecutor skeleton; orchestrator step loop with the cross-case hooks; timers and restart recovery; POST /events and GET /cases/{id}; property-based state machine tests; ADR 0026 (T90 kept) and implementation notes on 0004, 0005, 0007, 0008. Owner decisions taken at the start of the phase: keep T90 and test it; resolve spec conflicts in-flight and log them; fix safety-review findings before reporting | Owner approves Phase 1, then Phase 2 |
| 2026-11-03 | 2 | opusplan (Opus; desktop session) | not recorded | Cards P2-01..P2-11: the weight-dispute pack (eligibility, evidence plan, assess, action menu), the calculator and rate card, a seeded synthetic generator with 15 named fixtures, `SyntheticShipmentSource` with injectable faults and its contract suite, the gather round (D41 retries counted per round), ledger reconciliation and the three provable credit reasons, the rules-only baseline B1-B9 and its `rules_only` adapter, the orchestrator wiring for eligibility/gather/assess, hypothesis property tests on the calculator, ADR 0027. `EvidenceStore`, `AssessmentStore` and `packs/weight_dispute/pack.py` were specified but on no card, and were built here. Safety review: 2 HIGH (a resting gather round armed no timer, so a transient failure stalled the case forever; `assess` read the charge from the first DEBIT in the round regardless of claim line) and 6 MEDIUM, all verified in the code and fixed; 4 recorded in `BACKLOG.md` | Owner approves Phase 2, then Phase 3 |
| 2026-11-04 | 3 | Opus (desktop session) | not recorded | Cards P3-01..P3-11: owner setup guide (pulled forward) and `.env.example` check; contract suites for MessageChannel and TicketingSystem with deliberately unkind fakes; message templates and R09 number validation; contact limits (found dead: the counters were never populated); the Outlook adapter (draft, mark, send, read back; encrypted token cache, no plain-text fallback) and the Jira adapter (create then mark, never search-then-create); executor verification against both; approvals store and Streamlit review queue (hash-bound, reviewer fixed by session); reply poller (exactly-one routing, auth results that only add caution, bounce heuristic); timeline page (seller text escaped and labelled); `make demo` (refuses without credentials); ADR 0028. Owner decision: investigate wired to the rules-only reasoner - a dispute now reaches the review queue with a drafted, number-checked, hash-bound email. Found while wiring: cases were saved once per advance, which the database refuses as an illegal jump; now saved per transition. Two safety reviews: 12 + 8 findings (5 HIGH), all verified in code and fixed or recorded - including timed-out and 5xx sends recorded FAILED (would have allowed a second email; now UNKNOWN after a lookup, with executor.md 5.1's attempts rule as backstop), the Jira token in repr, and a review page that showed no email. Not done: the four stations after approval (apply_approval, execute, verify, SQLite ActionStore), declared timer effects, investigate audit events; live integration run pending owner accounts | Owner decides: build the sending stations, then Phase 4 |
| 2026-11-04 | 4P | Opus (desktop session) | not recorded | Owner re-plan (ADR 0029): a prototype Foundry agent first. Built offline-first: prompt registry and `neutralise` fence; model gateway output checker (one retry), scripted fake and contract suite; model-backed reasoner with briefing caps and post-schema checks (menu, cited ids, parameters, pattern claims); investigate falls back to rules only when the model is unreachable; spending guard (per-case cap, lifetime cap, credit window) with a call log in its own file (ADR 0030); Foundry Agent Service gateway (Entra ID only, pinned version, drift check, error mapping, worst-case costing), `make agents-sync`, `make foundry-check`, `make try-agent` (sends nothing), secret scan, guarded-path architecture test. Found in the installed SDK: calling by agent name is preview-only and Foundry rejects underscores in agent names; ADR 0029 addendum. Safety review: H1 the fence could be bypassed by a malformed tag swallowing the real closing tag (fixed: every tag-opening `<` escaped after NFKC); M1 billed timeouts and filter stops recorded as $0 (fixed: worst-case charge carried on errors); M2 unmapped SDK errors unrecorded (fixed); M3 a rejected answer followed by a timeout fell back to rules (fixed: becomes invalid_model_output); M4 auth and prompt errors left cases without a reason (fixed); LOWs fixed except two recorded in BACKLOG | Owner: Azure setup per SETUP_FOUNDRY.md, then `make try-agent`; then Phase 5 or 4+ |
| 2026-11-04 | 4P live | Opus (desktop session) | about $0.10 of a $2 owner allowance | Owner set up Foundry. Sign-in without the Azure CLI (`make foundry-login`, browser or device code, pinned tenant, encrypted cache). First live runs found: reasoning tokens exhausting the 1,200 output limit (now 4,000), a misleading 'not JSON' for cut-off answers, the wrong endpoint pasted (now refused by name), requested items run together in the email (Phase 3 template bug), fixture notes never read so `injection_in_ops_note` tested nothing (now outside text), and the trial allowlisting every fixture's own seller (now only the test seller). All 15 fixtures run live end to end; both injection cases flagged; spend cap and credit window refuse live at $0. Findings for 4+ in BACKLOG | Owner: Phase 4+ |
| 2026-11-04 | 4+ and 5 | Opus (desktop session) | $0 (offline) | Phase 4+ (ADR 0031): reply interpreter and drafter with number checks, two-pass caution ranking, rules-based input screen, injection corpus by every route, Haiku failover (F01) with per-route caps, Prompt Shields annotations, allow-listed tracing, spend report, eval harness. Phase 5 (ADR 0032): SQLite action and proposal stores, independent simulated ledger with every failure mode, apply/execute/verify stations, credit approval with amount, verified credit then approved confirmation, offline demo; S01 path matches the catalogue exactly. Safety review: no HIGH; MED - the claim was not saved before the external write (a crash erased the only sign a write may exist), R21 ignored screen errors / truncated / fabricated references, the confirmation's credited amount vouched for itself, ledger reads ran outside the write lock; LOW - blocked executes treated as failures, verify resting on any not-sent, direct-route billed failures charged $0, failover skipping the per-case call limit. All fixed, each with a test shown to fail against the old code | Owner: sign off; live eval (`make eval --live`, about $0.30) for the P4b-11 model choice when approved |
| 2026-11-04 | 4b eval | Opus (desktop session) | $0.07 (owner approved) | First live eval: Foundry agent (gpt-5-mini) 62% correct first action on 45 scenarios vs 48% for rules only, 7 regressions. Strong on credits (10 of 11) and on asking for evidence; under-escalates (defaults to asking the seller), never asks for remeasurement or upholds. Results in `docs/evals/2026-11-04-live-gpt-5-mini.md`; prompt work in BACKLOG. Total Foundry spend $0.17 of $150 | Owner: sign off 4+ and 5; for O1, a gpt-5.4-mini deployment to compare |
| 2026-11-04 | gaps (ADR 0033) | Opus (desktop session) | $0 (offline) | The inbound half: transition effects (timers, supersede/expire, in-flight to UNKNOWN on escalation, outcome outbox - none were applied before), the dispatcher (routing, P1-P4, principal and hash checks, the acceptance matrix, defer and redelivery, hand-off, human return), typed inbound payloads (the inbox stored `{}` before), reminder step, reply round with real contact counters, R12, R19 wait, message counts at planning, step events, one line assembly (`api/line.py`) and a runner (tick, recovery). Safety review: 2 HIGH (endless redelivery; a person could never return a case) + 6 MED, all fixed with tests that fail on the first version. `make verify` green (2813 passed) | Phase 6, starting with the runtime wiring and the scenario simulator |
| 2026-11-04 | 6 | Opus (desktop session) | $0.21 live red team (owner approved, cap $0.50) | P6-01 scenario simulator: all 57 non-Phase-7 scenarios on the whole line with recorded answers behind the real spending guard; it found and fixed a dozen real defects (escalation reasons, module checks before model calls, R05 clarification exemption and timezone, R24 repeat signal, ledger retries and lookup-before-FAILED, eligibility from evidence, hand-off notice, double-armed retry). P6-02/03/07 metrics, fail-closed gates (config/release_gates.yaml, measured_by recorded vs live) and the report; P6-04/05/06 drills; the running app now runs the whole line (Line + Runner, /inbound, kill switch row, lifespan runner); P6-08 security review (T03 sender auth and T09 approver list built); P6-09 README, guides and claims test; P6-11 local live red team (D35), 0 of 24 through by statement and notes; P6-13 ADR 0034. Owner decisions D46-D48. Two safety reviews (inbound half; Phase 6: 1 HIGH kill-switch setting ignored, 3 MED, 5 LOW) all fixed with tests. `make verify` green (3082 passed) | Owner: create Application Insights for P6-12; sync the reply-reader agent for the third red-team route; then Phase 7 |
| 2026-11-04 | 7 | Opus (desktop session) | $0 (offline) | HOOK A connected in the kernel first, disclosed (ADR 0035, D49), tagged `phase-6-complete`; then zero kernel changes. P7-01 migration 0002 with six module-owned tables; P7-02 Sql provider (5 newest, no self-evidence, failure is caution) and P7-07 observer, both on the existing contract suites; P7-03 deterministic clustering; P7-05 the parent Jira investigation through the kernel's PolicyEngine and ActionExecutor with a module-owned ActionStore (ADR 0036); P7-04 pattern_analyst behind code checks (PA-E1..E3, manipulation), prompt examples fixed to match the schema, status kept inactive; P7-06 two-pass wired, code decides `changed_recommendation`, call limit 10 with patterns; P7-08 confirmation on ops findings only (ADR 0037); P7-09 recurrence in the eval report; P7-10 S15 and S16; P7-11 proofs; P7-12 enabled. Found and fixed on the way: every pattern lookup timed out on the case's own write lock (deferred reader engine), the observer was called inside the runner's lock, and the outcome outbox could not read back what it stored, so the first closed case made every runner tick raise (a Phase 5 defect) | Owner: sign off Phase 7; push; paid live run of pattern_analyst when wanted |
| 2026-11-04 | 7 close | Opus (desktop session) | $0.28 pattern_analyst live (owner approved, cap $0.50) | Safety review 0 HIGH / 3 MED / 5 LOW, all fixed with tests; pattern_analyst live eval FAIL (3 of 6 under the pre-set all-pass rule; injection held in all 24 answers; two prompt defects fixed), prompt stays inactive (D53); owner approved all phases (D54); P6-12 still waits on Application Insights | Owner: Application Insights for P6-12; decide PA-E1 strictness before another paid analyst run |
| 2026-11-04 | 6 (P6-12) | Opus (desktop session) | $0 (Application Insights free allowance) | Owner created Application Insights (South India, on the existing Log Analytics workspace) and set the connection string in `.env` by clipboard, never in chat; `tests/integration/test_observability.py -m integration` passed: a traced case reached the service and the content sentinel appeared in no exported span (I14) | none for Phase 6 |
| 2026-11-04 | e2e (owner request) | Opus (desktop session) | $0 (offline) | New end-to-end test on the running app: HTTP dispute, duplicate recognised, INR 1,820 credit approved in the review console, posted and read back, confirmation approved, sent once and read back, RESOLVED_CREDITED, outcome handed to the cross-case module, timeline served. Found and fixed (D56): the kill-switch read waited on the case's own write lock, so the running app refused every credit and email | none |
| 2026-11-04 | 7 analyst | Opus (desktop session) | $0.032 (within the owner's $0.50 cap; $0.31 in all) | PA-E1 kept strict and the prompt aligned (D57); live run 5 passed 6 of 6, first try each, injection ignored; pattern_analyst v1 activated and wired for when the model route is on | Owner: Outlook and Jira test accounts |
| 2026-11-04 | 3 live (Jira) | Opus (desktop session) | $0 | Owner created a personal Jira site and project WD; live contract suite green; live end-to-end: the cross-case investigation WD-42 opened through the executor and read back, no duplicate on re-run. Fixed: the Jira lookup's KeyError on the new search API; contract keys now fresh per run; real Jira wired into startup behind `config/modules.local.yaml` | Owner: Outlook when the personal Azure directory exists; tidy the WD test issues if wanted |
| 2026-11-04 | email (Gmail) | Opus (desktop session) | $0 | Owner chose the Gmail API over Outlook (no Microsoft directory). Tagged `phase-7-complete`; one disclosed kernel line + spec row under ADR 0038; kernel rule now: Phase 7 diff empty forever, later kernel commits name an ADR. Built `gmail_api` (PKCE loopback sign-in, encrypted refresh token, send, two markers, read-back, replies, bounces); 26 offline tests against a stand-in Gmail; joined the live contract suite | Owner: Google Cloud setup and `make gmail-login`, then the live run |
| 2026-11-05 | agent frameworks | Opus (desktop session) | about USD 0.21 (owner cap USD 0.50) | Case Explainer on the Claude Agent SDK (read-only tools, SQLite mode=ro, no built-ins, deny hook, Haiku only, capped; live check 3 of 3 for USD 0.003); LlamaIndex BM25 policy lookup (local, about 245 passages); LangGraph two-pass investigator (recorded 57 of 57 after fixing a script-like stand-in and doubling its call cap; live inconclusive: the 45 scenarios rest on 13 disputes). Explainer found an ineligible case does not store its reason | Owner: decide whether to fund a per-dispute repeated live evaluation |
