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
