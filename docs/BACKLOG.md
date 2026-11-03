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
