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
