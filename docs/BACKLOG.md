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
