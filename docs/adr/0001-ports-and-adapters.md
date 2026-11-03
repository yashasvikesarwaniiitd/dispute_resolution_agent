# ADR 0001: Ports and adapters, with a pure kernel

Status: accepted (owner, 2026-11-03)
Date: 2026-11-03

## Decision
The kernel owns every abstraction it depends on, as `typing.Protocol` classes in `kernel/ports/`, and imports only the standard library and pydantic. Adapters live outside it in `modules/`, `packs/` and `infra/`. One composition root, `app.py`, is the only place that constructs an adapter or reads the environment, and nothing imports it. Three import-linter contracts enforce the layering, and they are checked against `ARCHITECTURE.md` section 2 so they cannot be weakened quietly.

## Why
In plain terms: the decisions this system makes about money have to be testable without a network, a database or a model, and they have to stay testable when all three are swapped out.

A dispute is decided by code that is pure in the useful sense — same inputs, same answer. Keeping that code free of adapters is what lets a scenario spanning five days and six external calls run in a millisecond and give the same result twice, which is the foundation every eval in Phase 6 stands on. It also means the AI cannot reach a write method by accident (I1): the `Reasoner` port takes a briefing and returns a proposal, and there is no store, channel or executor in its signature to misuse.

The purity is also what makes the project's own rule enforceable. "Kernel imports only the standard library and pydantic" is a sentence anyone can agree with and nobody can check by reading; `make arch` checks it on every commit.

## Options considered
- **A layered application with the database at the bottom.** Conventional and familiar, but it inverts the dependency that matters: the decision logic would import the repository, and testing a policy rule would need a database. Every later phase would pay for that.
- **Ports defined next to their adapters.** Tempting, because the adapter author knows the external system best. Rejected: the kernel would then depend on something it does not own, and the shape of a port would drift towards whatever the current adapter finds convenient — which is how a `MessageChannel` ends up with a Graph-shaped method on it.
- **Dependency injection through a framework container.** Rejected under YAGNI (CLAUDE.md section 6). Constructor arguments and one composition root are enough at this size, and a container hides the wiring the `/healthz` endpoint is meant to expose.

## Consequences
Easier: a new adapter is a new file plus a line in `modules.yaml`; the contract suite says when it is done (CLAUDE.md section 8). Unit tests need no fixtures beyond frozen models. The cross-case socket (ADR 0002, D3) is connectable without touching `kernel/`.

Harder: one file knows about everything, and it is `app.py`. Every port needs a fake as well as a real adapter, which is a real cost paid once per port. Adding a method to a port means adding it to every adapter, including the `Disabled` ones — the parametrised test in `tests/unit/modules/test_disabled_adapters.py` exists to make that failure loud rather than silent.

Watch: the pressure to weaken "Kernel is pure" will come from a genuinely convenient import, not from carelessness. `tests/architecture/test_imports.py` compares the shipped contracts with the specification field by field, so removing one forbidden module fails as loudly as deleting the contract.

## Sources
Project documents only: `CLAUDE.md` sections 4 and 6, `docs/ARCHITECTURE.md` sections 2-4, `docs/spec/kernel-interfaces.md` section 6. No external documentation was needed.
