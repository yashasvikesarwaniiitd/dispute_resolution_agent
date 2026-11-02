# Phase 0: Foundations and sockets

## Role
Principal engineer setting up a repository that must stay clean through seven more phases.

## Context
Read `CLAUDE.md`, `docs/ARCHITECTURE.md` and `docs/FLOW.md`. Nothing exists yet. This phase writes no business logic. It builds the skeleton, the tooling and every socket (port) that later phases plug into, including the empty socket for cross-case intelligence.


**Task cards: `docs/spec/task-cards/phase-0.md`.** They are the executable form of the tasks below: each names its files, the spec sections it implements, the tests to write first and the command that proves it. Where a card and this prompt differ, the card is newer — it was written against the frozen `docs/spec/` (`CLAUDE.md` section 18). Read `docs/spec/INDEX.md` and `docs/spec/escalation-guide.md` first.

## Objective
A runnable, empty, fully gated project in which the dependency rules are enforced by tests, modules can be switched on or off by configuration, and an unconnected cross-case socket already exists.

## Scope
### In scope
Repo scaffold, tooling, domain primitives, all ports, `Disabled` and `Null` adapters, module registry with startup validation, composition root, logging, clock and id infrastructure, architecture tests, CI, first ADRs.
### Out of scope
State machine logic, persistence, calculations, real integrations, any LLM call.

## Tasks
1. Scaffold the repo per `docs/ARCHITECTURE.md` section 3 with `uv`. Dependencies: pydantic, pydantic-settings, fastapi, uvicorn, sqlalchemy, alembic, structlog, pyyaml, jinja2; dev: pytest, hypothesis, ruff, mypy, import-linter, pre-commit.
2. Add the Makefile targets from `CLAUDE.md` section 15, pre-commit hooks, and a CI workflow that runs `make verify`.
3. In `kernel/domain`: ids, `CaseState` (10 working states and 6 outcomes), `Money` (Decimal, INR), typed errors including `ModuleUnavailable` and `GuardrailBlocked`, and the models `PatternRecord`, `CautionSignal` and `CaseOutcome`. Construction and validation only.
4. In `kernel/ports`: every port listed in `docs/ARCHITECTURE.md` section 4, including `PatternContextProvider`, `CaseOutcomeObserver`, `ModelGateway` and `InputScreen`.
5. Add a `Disabled` adapter for each module port (raises `ModuleUnavailable`), and `NullPatternContextProvider` and `NullOutcomeObserver` (empty list, no-op). Add `NoopTracer`, a `RulesBasedInputScreen` stub and a `FakeModelGateway` with canned responses.
6. Build the module registry, `config/modules.yaml` and startup validation (unknown module, unknown adapter, missing dependency, required module disabled).
7. Write `app.py` as the only composition root, with an app factory and a `/healthz` route that lists each module, its state and its adapter.
8. Add structlog JSON logging with correlation ids and an email and name redaction processor; add `Clock` and `IdGenerator` with system and fake implementations.
9. Add architecture tests: the import-linter contracts from `docs/ARCHITECTURE.md` section 2, and a test that fails if prompt-like text appears inline in `src/` (see `prompts/README.md`).
10. Write ADR 0001 (ports and adapters), ADR 0002 (module registry and disconnect behaviour) and ADR 0003 (tooling). Create `docs/PROGRESS.md` and `docs/BACKLOG.md`.
11. Create a contract-test skeleton file per port in `tests/contract`, skipped with a reason that names its phase.

## Technical requirements
- Python 3.12+, `mypy --strict` clean, ruff clean.
- Domain models are frozen pydantic models. No I/O in `kernel`.
- `NullPatternContextProvider` is selected whenever `cross_case` is disabled.

## Acceptance criteria
- A fresh clone passes `make setup && make verify`.
- Adding `import dispute_agent.modules` inside `kernel` makes `make arch` fail. Keep this as a test that proves the contract can fail.
- Starting the app with `ledger` enabled and `approvals` disabled fails at startup with a message naming both modules (test name in the PR summary).
- Starting with `cross_case` disabled succeeds, and `/healthz` shows `cross_case: disabled` with the null adapter.
- Every port has a contract-test skeleton, and `ModuleUnavailable` is raised by every `Disabled` adapter (unit test).

## Deliverables
Repo skeleton, config files, ADRs 0001 to 0003, `docs/PROGRESS.md`, `docs/BACKLOG.md`.

## Constraints
I1 and I6 shape the port design. Do not add a dependency without an ADR line explaining why.

## Process
Plan mode first; at most 5 open questions with defaults; one task at a time; `make verify` stays green.

## Model and effort
Session on `opusplan`, effort medium. Opus plans, Sonnet builds. Escalate to the `architect` subagent if a port signature or a dependency rule has to change.

## Stop and ask if
A port signature in the docs looks wrong once you try to implement it, or a tool choice here conflicts with the current official documentation.
