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
