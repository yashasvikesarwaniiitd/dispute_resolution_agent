# Backlog

Ideas parked outside the current phase. Do not build from here without the owner naming it.

- Add tools to the Agent Service agents (needs an ADR and the owner's approval; it would weaken I1 if the tools can write).
- Evaluate Microsoft Agent Framework behind the `Reasoner` port.
- Graph change notifications instead of polling (needs a public endpoint).
- Postgres instead of SQLite.
- A second domain pack to prove the kernel is domain-neutral.
- Dashboards over `model_calls` and the event log.
- Foundry continuous evaluation on sampled traffic.

- Hosted (containerised) agents in Foundry.
- Claude on Foundry through a `foundry_anthropic` gateway (needs a project in East US 2 or Sweden Central and Marketplace billing; credits may not cover it).

- Correct `docs/ARCHITECTURE.md` section 5: it names `cross_case` as the only module
  wired to something other than its `Disabled` adapter when off. `tracing` is the second
  (no-op). `orchestrator.md` section 5 is the authority and already says so; ADR 0002
  records the reasoning. A documentation fix, not a behaviour change.
- Write a task card for `kernel-interfaces.md` section 4, which Phase 0 built because
  P0-05 could not type a port without it (see the Phase 0 note in `PROGRESS.md`).
- Read and link the SQLite documentation ADR 0008 asks for (STRICT tables, partial
  indexes, transactions, WAL). Phase 1 verified the behaviours empirically with fifty
  constraint tests instead, which is stronger evidence for this database but is not the
  same thing as a guarantee. See the implementation note on ADR 0008.
- Add the kill-switch-while-gathering scenario to eval-scenarios.yaml when Phase 2 (card
  P2-04) builds gather_evidence, so T90 from GATHERING_EVIDENCE is walked by the
  catalogue and not only by a unit test (ADR 0026).
- kernel-interfaces.md section 4.9 omits `Timer.gather_round`, which schema.sql requires
  and D41 depends on. The model carries it with the reasoning recorded; the interface
  listing should gain it at the next spec revision.

From the Phase 1 safety review (2026-11-03), carried rather than fixed:

- The policy engine's mandatory-rule check tests identity, not behaviour: a stub with
  `rule_id = "R13"` that returns ALLOW would satisfy it. The shipped rules are pinned by
  their own tests, so this only bites a future `phase_N_rules()`. A contract test keyed
  on rule id would close it ("whatever claims R02 must DENY when kill_switch is set").
- There is no SQLite ActionStore yet, so the executor is only exercised against a fake
  with no triggers. The claim that `tr_actions_approval_binding` and
  `ux_actions_one_credit` would refuse a bad credit even without the Python checks is
  true of the DDL and untested end to end. Phase 5 (cards P5-02, P5-03) should plan a
  credit against the real schema.
- There is no `KillSwitch` adapter in `src/` at all, and the orchestrator does not
  consult one; `settings.kill_switch` is read by nothing. I10 holds because the executor
  checks its own switch in `plan` and `execute`, but nothing can currently engage it.
  Phase 3 needs the adapter and the control_flags row behind it.

## Found during Phase 2 (P2-06)

- **`EvidenceStore` and `TimerStore` were never built.** `kernel-interfaces.md` section 6.2
  specifies both; no Phase 0 or 1 card creates them, which is the same gap as the section 4
  models. P2-06 works around it by keeping the gather round pure — it decides and returns
  the results, and the caller records them — which is the right shape anyway, but the ports
  still have to exist before P2-09 can wire the step into the orchestrator.
- ~~`TimerStore` has no way to count fired EVIDENCE_RETRY timers for a gather round.~~
  Withdrawn: `SqliteTimerStore.retries_this_round(case_id, gather_round)` was built in Phase
  1. `gather()` still takes the count as an argument, which keeps it pure and testable
  without a database; P2-09 supplies it from that method.
- **Reply interpretation is not in the gather round yet.** `orchestrator.md` has the step
  interpret unread seller replies into AI_DERIVED items. That needs `MessageRecord` (not
  built) and a `ReplyInterpreter` adapter (Phase 4). The source half is complete and tested;
  the reply half is a seam, not an omission to forget.
- **P2-07's card lists `modules/ledger/simulated/` among its files**, but its Build, Tests
  and Acceptance are entirely about `reconcile.py`, and the Ledger contract suite defers the
  simulated adapter to Phase 5 (card P5-02). Built `reconcile.py` only. If the intent was to
  build the adapter in Phase 2, that is a card conflict to settle before Phase 5.
- **`WeightDisputePack` does not yet satisfy `DomainPack`.** `render_template` is Phase 3,
  and the port declares `baseline_proposal(briefing)` while the pack needs an id and a
  timestamp it cannot mint itself. Both the orchestrator steps and the `rules_only` adapter
  therefore depend on narrow protocols (`CasePack`, `BaselinePack`) covering only what each
  uses — better design regardless, and it keeps the gap visible rather than casting over it.
  Settle the port's signature when Phase 3 adds `render_template`.
- **`config/modules.yaml` still has `rules_only` disabled**, with the comment "on from Phase
  2, when the pack baseline exists". It exists now. Turning it on is a composition-root
  change and belongs with P2-09's wiring being finished, not with the adapter being written.

## From the Phase 2 safety review (2026-11-03)

Two HIGH and six MEDIUM findings were fixed in the phase. Four are recorded here instead.

- **`credit_amount` still has no caller.** `reconcile.credit_amount` is the only code that
  nets a credit against earlier ones (money-rules.md section 3), and nothing in `src/` calls
  it: `orchestrator.md` section 3.6 says the credit amount is `assessment.discrepancy`,
  which carries no netting. The assessment now carries `recalculated_remeasured` and
  `duplicate_debit_total` so the amount *can* be derived from the proven reason, but the
  derivation belongs with the proposal-to-intent step in Phase 4. **Until that is wired, do
  not take `assessment.discrepancy` as the credit amount**: money-rules' own worked example
  (case A credited 200, case B proves 600 → B owes 200) would pay 400.
- **B7's "no evidence request sent yet" is approximated by `gather_round <= 1`.**
  `agent-design.md` section 5 says what it means; `ContactStatus` carries
  `clarifications_sent` but no evidence-request flag, so there is nothing exact to read. It
  is mostly shielded by B5 — any round carrying a reply escalates first — but it is a
  substitution, not a reading. Add the field to `ContactStatus` when Phase 3 builds the
  briefing for real.
- **`SqliteEvidenceStore.bundle` does not verify `content_hash`.** The hash is computed and
  stored for exactly this check and never recomputed on read. Low while the case database is
  the only writer; worth doing when anything else can write evidence rows.
- **`DomainPack.assess` has no way to say which charge line a dispute is about.** The pack's
  `assess` now takes `claim_ref` as a keyword the port does not declare, because without it
  the pack took the first DEBIT in the round and a shipment with two charge lines
  recalculated one dispute against the other's charge. Settle the port's signature (add
  `facts` or `claim_ref`) with an ADR when Phase 3 revisits `DomainPack`.

## Found during Phase 3

- **Nothing in `src/` constructs a `PolicyEngine`.** `phase_1_rules` and now `phase_3_rules`
  are exported and exercised only by tests; the composition root does not wire policy yet.
  That is fine while no step plans an action for real, and it stops being fine the moment
  `make demo` runs — P3-10 wires it, and must pick `phase_3_rules` so R09 is in the set.
  A rule catalogue nothing instantiates is the same failure class as a counter nothing
  populates, which is what this card found.

## Phase 3 scope boundary (read before Phase 4)

- **`make demo` cannot complete its documented run, and P3-10 is therefore partial.** The
  phase exit asks for "a real approved email and a real Jira issue, both verified by
  read-back". Every piece exists and is tested: the adapters, the executor's verification,
  the approvals queue, the templates. What is missing is the orchestrator's `investigate`
  step, which still escalates because it has no reasoner — so nothing produces the message
  intent that the rest of the chain would carry.

  The `rules_only` reasoner built in P2-08 could fill that gap today, and wiring it is a
  small change. It is **not** done here because that wiring is card P4-09's, and quietly
  expanding a phase to make its own demo pass is how a phase boundary stops meaning
  anything. The demo refuses to run without credentials and says what it would need, rather
  than running against fakes and appearing to prove something.

  **Decision for the owner:** either wire `investigate` to the rules-only reasoner now (a
  one-card addition to Phase 3), or accept the demo as deferred to Phase 4 and treat this
  phase-exit line as met by `make test-integration` instead.

### Update (owner decision: wire investigate now)

`investigate` is now wired to the rules-only reasoner (owner's call). A dispute runs from
RECEIVED to AWAITING_APPROVAL with a drafted, number-checked, hash-bound email in the review
queue. The demo still cannot **send**, because four more stations are empty:

1. `apply_approval` — turns an APPROVED decision into a planned action (T10).
2. `execute` — calls the executor to send (T17).
3. `verify` — reads the message back from Sent Items (T18).
4. A SQLite `ActionStore` — where the executor records every action. Without it there is
   nothing to plan *into*, and the duplicate-send protections have no record to read.

Plus the path that carries your decision from the review console into the case (ADR 0013
says it arrives as an APPROVAL_DECIDED inbound event; the console currently writes the row
directly and nothing advances the case afterwards).

**Declared timer effects are never carried out.** `transitions.yaml` tells T07 to arm
APPROVAL_EXPIRY, T-rows into AWAITING_SELLER to arm reminders, and so on. The state machine
hands these to the caller as `Applied.effects`, and no caller performs them. The only timer
that is ever set is the evidence retry the gather step arms itself. Consequence today:
an approval nobody answers never expires, and no seller reminder ever fires. Safe in the
direction that matters - nothing is sent without approval - but it needs one component that
applies `schedule_timers` / `cancel_timers` in the same transaction as the transition.

**Fixed while wiring:** the runtime saved a case once at the end of `advance`, which the
database correctly refuses as an illegal jump the moment a case moves more than one state.
It only worked before because every case stopped one step from where it started. Cases are
now saved after every transition (`advance(..., commit=...)`), as orchestrator.md specifies.

### From the Phase 3 safety review

- **The SQLite ActionStore's first tests** (finding 6): `outbound_message_count` counts SEND_MESSAGE
  rows in SENT, VERIFIED and UNKNOWN; a FAILED row is not counted; a re-executed row is
  counted once; the `since` bound is respected. The stub-based tests that claimed this were
  removed because they could not fail.

### From the second Phase 3 safety review (recorded, not fixed)

- **The investigate step writes no audit events of its own.** orchestrator.md section 3 lists
  PROPOSAL_RECORDED, INTENT_CREATED, POLICY_EVALUATED and APPROVAL_REQUESTED for this step.
  I8 holds - every state change has its STATE_CHANGED event - but the diary does not record
  *why* the case moved or that an approval was requested. A step can only return a trigger
  today (`StepOutcome`), so steps need a way to hand events to the loop. Do it before the
  model reasoner lands in Phase 4, when "why did it decide that" becomes the question.
- **The briefing's contact status is hard-coded** (`reply_in_round=False`,
  `clarifications_sent=0`). True for every case that can reach the station today; wrong the
  moment the reply poller feeds replies into cases, when a later round would draft an
  `evidence_request` instead of a `clarification_request` and the one-clarification cap
  (D19) would not be checked at planning. Wire it together with the poller.

## Phase 4P (prototype agent) - deferred, by ADR 0029

- **Proposals are not stored, and the investigate step still writes no audit events.** The
  model's proposal carries its prompt name, version, hash and model id, but nothing persists
  it, and REASONER_FALLBACK is not written when the rules stand in. The model-call log
  (`data/model_calls.sqlite3`) records every call's cost and outcome, so spend is auditable;
  *why a case moved* is not yet. The second Phase 3 review asked for this before the model
  reasoner landed; ADR 0029 moved it to Phase 5 with the sending stations, because the
  prototype cannot send anything. It must land before any proposal can lead to a send.
- **Briefing total-length shrink order** (agent-design.md section 1) is not implemented. With
  every per-field cap applied the briefing cannot exceed the total; needed if the caps change.
- **Model-written requested items reach the email body.** `request_seller_evidence` items are
  model text (at most three, 200 characters each), rendered into the template. Every message
  kind needs approval today, so a person reads them before sending; R09 checks any numbers.
  Phase 4+ should screen them like seller text before auto-send is ever enabled for any kind.
- **Phase 4+ (ADR 0029):** Haiku failover route (F01), `foundry_guardrails` screen from the
  content-filter annotations, tracing, the eval suite and model choice, credit-window report,
  the model reply interpreter and drafter, two-pass anchoring, the provider schema and a
  `text.format` attempt at sync, live integration run of the Foundry contract suite.
- **The cap is checked before the call, the cost known after**: the last call can exceed the
  cap by one call's price. A pre-call estimate (prompt tokens + `max_output_tokens` at output
  price, foundry.md section 10) would close it.
- **From the Phase 4P safety review, accepted rather than changed:**
  - *The reasoner holds a gateway object whose guard has a ledger with a `record` method.*
    The reasoner is typed against the `ModelGateway` port (one method, `complete`), and the
    ledger writes only spend rows, never case state. Python cannot hide an attribute; a
    narrower wrapper adds code without changing what the type allows. Revisit if a reasoner
    ever gains code that inspects its dependencies.
  - *The per-case cap counts guarded calls, and one guarded call can include its retries*
    (up to 2 answer attempts x 3 transport attempts). Each retry is costed, so the lifetime
    cap sees all of it; the policy comment now says what is counted.

## From the first live Foundry runs (2026-11-04, all 15 fixtures, about $0.10)

Inputs for the Phase 4+ eval suite and prompt v2 (the v1 prompt is frozen):
- **Number words in requested items.** The agent wrote an item with a number word; the
  template's MD-F1 guard blocked the draft and the case went to a person. Safe, but a good
  case lost. v2 should instruct digits only in `requested_items`.
- **The agent upholds more readily than the rules.** It upheld on `conflicting_history`,
  `hub_scanner_cluster` and `injection_in_statement`, where the baseline escalates or asks
  for evidence; it asked for evidence on `ambiguous_image`, `kurta_box_larger` and
  `injection_in_ops_note`, where the baseline escalates. Agreement 6 of 13. An uphold is a
  "no money" decision: the eval suite must judge these before `upheld_explanation` exists.
- **Decisions vary between runs** (`scanner_fault`: request_seller_evidence, then
  request_remeasurement). Evals should run each case several times.
- **Injection flagging held** on both injection cases, on repeat runs.
- **Reasoning tokens**: about 500-1,150 per case before the answer; the 4,000 output limit
  leaves room. Cost about $0.004-$0.012 per case.

## Phase 4+ and Phase 5 (2026-11-04) - done, and what is left

**Closed by this work:** proposals are now stored with prompt and model (the audit gap); the
four sending stations (apply_approval, execute, verify, SQLite ActionStore); the case records
its pending plan; credit flow and verification; credit approvals show the evidence; the
upheld and credit-confirmation templates; module-unavailable is named on the case (S41).

**Still open** (updated after ADR 0033, the inbound half):
- **The long-running runtime** (`make run-api`) still assembles the Phase 1 steps, and
  `build_adapters` still builds only Disabled adapters. Wire `_build_runtime` to `api/line.py` and
  `api/runner.py`; build the synthetic, fake/real email and Jira, simulated ledger and store-backed
  approvals adapters from `config/modules.yaml`; a shipment source that serves any synthetic
  shipment (not one fixture); `POST /events` for every inbound type; a background tick and
  `recover()` at start-up. Moves into Phase 6 with P6-01 (the simulator needs the same assembly).
- **Live demo against Outlook and Jira** needs builders for the real adapters inside the line and
  the owner's accounts (`docs/SETUP_CHANNELS.md`).
- **handed_to_human_notice template**: a reviewer who asks to notify the seller on hand-off gets
  NOTICE_NOT_SENT today (T36); add the template and the T34/T35 path.
- **Outbound message rows**: `messages` holds inbound replies only; outbound sends are not
  recorded there, so conversation routing works only after the seller's first reply in a thread.
  Sender + shipment-in-subject routing covers the first reply.
- **R11 (draft blocked) and R24 (repeat dispute)** are still carried by steps, not rules.
- **Before any external inbound endpoint exists** (safety review of ADR 0033, LOW): tie HUMAN_*
  and APPROVAL_DECIDED events to an authenticated `human:*` actor and the `approvals` /
  `ops_console` sources (P3 trusts the declared actor today); do not trust `event.case_id` from
  sources that have their own routing rule; put the decided approval or resolved action on the
  input event's payload so the trail, not only the stores, says who decided what.
- **P1 version check**: pre-dispatch rule P1 does not compare `armed_at_version`; cancel and void
  cover every current path, but the spec names the version check too.

**Closed by ADR 0033:** the seller-reply round, declared timer effects (reminder, decision deadline,
ops timeout, approval expiry), step events (INTENT_CREATED, APPROVAL_REQUESTED, REMINDER_SKIPPED,
the effect events), the dispatcher and the runner.

## From the first live eval (2026-11-04, gpt-5-mini, $0.07)

Agent 62% vs rules-only 48% on first-round actions. Prompt work for `case_investigator/v2`
(a released prompt is never edited), each change followed by the same live eval:
- **When to hand to a person.** Seven escalation scenarios got "ask the seller" (S05a, S14a,
  S18b, S21, S29b, S43b) or a remeasurement (S40). Make the escalation triggers explicit.
- **When to ask ops to remeasure** instead of the seller: 0 of 5 (S03, S04, S12c, S31, S44).
- **When to uphold**: S09a never upheld.
- **Credits a person should decide** (S24, S43a; the baseline does the same): check which rule
  should catch them in the full flow, and add a scenario test if none does.

## Phase 6 - open items (2026-11-04)

- **P6-12 tracing check - DONE 2026-11-04 (D55).** Was waiting on the owner: create Application Insights, put its connection
  string in `.env` (`APPLICATIONINSIGHTS_CONNECTION_STRING`), `uv sync --group telemetry`, then
  `uv run pytest tests/integration/test_observability.py -m integration` and look in the portal
  (Application Insights > Search). The exporter is a beta (`azure-monitor-opentelemetry-exporter`
  1.0.0b58): opt-in group only, never in `make verify`.
- **Red team, reply route live**: `make agents-sync` creates only the investigator agent. Extend it
  to sync `seller_reply_interpreter` (owner runs it), then `make red-team ARGS="--live"`.
- **Live quality gates** fail for gpt-5-mini today (62% of 45; D47 keeps the bars): write
  `case_investigator/v2` from docs/evals/2026-11-04-live-gpt-5-mini.md, then a live run.
- **The API has no sign-in**: loopback only; `/inbound` takes replies and bounces only. Ops findings,
  withdrawals and people's decisions need authenticated sources before any wider exposure.
- **T18**: test the review screen's order and labelling.

## Phase 7 - open items (2026-11-04)

- **pattern_analyst activation - DONE 2026-11-04 (D57): live run 5 passed 6 of 6; active, used when model_gateway is on.** Was (D52): The recorded eval suite passes (`tests/evals/pattern_analyst`). Activation needs a live run on the real model, which is paid; until then patterns keep their code-written summary. The live call is also not yet wired into the running app (`build_cross_case_job(analyst=...)` is passed None); wire it together with the paid run.
- **A case-less action socket.** The investigation borrows an in-memory subject case and a module-owned ActionStore (ADR 0036). A later kernel version could add a first-class "action without a case", which is cleaner; it is a kernel change and needs an ADR.
- **`CaseOutcome.used_pattern_ids` is never filled.** The observer over-approximates which cases could have seen a pattern (ADR 0037). Filling the field in the kernel would make the exclusion exact; it is a kernel change.
- **Investigation text is fixed at first detection.** Later members are linked in our tables, not added to the Jira issue: the TicketingSystem port has no update operation.
- **Clustering limit.** A narrow cause inside a broader group is reported as the broad group (ADR 0036). Only real data would show whether that matters.
- **Reply interpreter and drafter prompts** are still `inactive` (no live eval run yet), like the investigator's v1.
- **A dismissed key never comes back.** One pattern per (dimension, value), and DISMISSED is permanent (ADR 0037). A new problem on the same scanner later needs a person to reopen it; consider a "re-detect after N days" rule with its own ADR.
- **UNKNOWN investigations reach a person through the logs only** (`pattern_investigation_needs_person`, `Investigations.needs_person()`). The review console has no view for them yet.


## Making the engine multi-domain (2026-11-05, from the README repositioning)

- **Neutral core names before a second domain pack.** The pack socket (`kernel/ports/domain_pack.py`) is generic, but the core still says "shipment" and "seller" (`CaseFacts`, about 37 kernel files), the action menu has `request_remeasurement`, and `IssuePayload.kind` only allows `remeasurement`. A second domain needs neutral names (item, customer, request re-check) first: a kernel change, so an ADR, after `phase-7-complete`.
- **A second pack as the proof.** For example marketplace fee disputes or B2B invoice disputes, reusing the engine unchanged after the rename.

## Found by the Case Explainer's live check (2026-11-05)

- **An ineligible case does not store why.** `REJECTED_INELIGIBLE` cases have empty `review_code` / `review_detail`, so the record cannot say which eligibility rule failed (for example `not_delivered`); the explainer had to say so. Store the eligibility reason code on the case (kernel change: an ADR).

## Live evaluation design (found 2026-11-05, ADR 0041)

- **Score live accuracy per distinct dispute, over repeated runs.** The 45 live scenarios rest on 13 fixtures, and `kurta_box_larger` alone decides 29 of 57 scenarios, so one model coin-flip moves the score by tens of points (62% of 45, then 24% of 45 on an identical rerun). Suggested: 13 disputes × 5 runs × each engine, reporting per-dispute agreement. That is about USD 0.5 to 0.7 and needs the owner's approval. Until then the live score is reported as unsettled.


## Found by the full live run (2026-11-07, Gmail + Jira + ledger)

- **Done 2026-11-07 (ADR 0043, case_investigator v2): closed list of requested items.** Kept for the record: **AI-worded evidence requests trip the number-word rule.** With the live Foundry investigator on `scanner_fault`, the AI rightly chose `request_seller_evidence`, but its own wording of the requested items rendered a number word (for example "two photos"), and R09 / MD-F1 blocked the draft, so the case went to a person (`policy_escalated: draft_blocked`). Safe, but at scale many AI-handled evidence requests would reach a person. The rule is right (a figure in words cannot be checked); the fix is upstream: either the investigator picks requested items from a fixed list whose wording lives in the template, or its prompt requires digits for counts ("2 photos"), then a live check. A prompt change needs a new version and an eval run (CLAUDE.md section 9). USD 0.015 spent finding it.
- **Seller replies need the AI.** By design the rules-only engine handles the first round only; a free-text reply escalates (`proposal_escalated`). A live end-to-end with the rules engine therefore stops after the reply. Worth stating in the demo guide.
- **The full live run passed with recorded decisions.** `scanner_fault` with the suite's recorded answers ran through real Gmail (2 emails, both read back from Sent), real Jira (WD-43, read back) and the simulated ledger (credit read back) to `RESOLVED_CREDITED`, 24 states, matching S06. The seller reply and the hub finding were simulated inputs; a real Gmail reply and a real Jira comment are not yet exercised live.
- **Sync the hosted agent to case_investigator v2.** Owner step: `make agents-sync` (ADR 0043). Until then the hosted route (`foundry_agent_service`, used by `--agent` and the live eval) sends cases to a person with `agent_drift`; the direct route already uses v2.
- **case_investigator_agent v2 should copy the closed list** before the agentic engine is ever activated (ADR 0043).
- **A claimed but absent attachment.** Live, after a simulated reply saying "photo attached" with no photo in the evidence, the AI re-requested the photos instead of going to the hub (S06's recorded path). Cautious and allowed; worth a scenario with a real attachment, and part of the per-dispute live evaluation.
