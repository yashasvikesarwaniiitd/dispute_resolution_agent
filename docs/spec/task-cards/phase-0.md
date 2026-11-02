# Phase 0 task cards — Foundations and sockets

Stage A5. Source prompt: `docs/build-prompts/phase-0-foundations.md`. Phase outcome: a runnable, empty, fully gated project in which the dependency rules are enforced by tests, modules can be switched on or off by configuration, and an unconnected cross-case socket already exists.

**How to use a card.** Each card is self-contained: it names the files to create, the spec sections that decide their content, the tests to write first, and the command that proves it. Build cards in dependency order. `make verify` stays green at the end of every card. If a card cannot be built without making a design decision it does not state, stop — the missing decision belongs in `docs/spec/`, which is frozen at `arch-v1`, so it needs an ADR and the owner's approval (`docs/spec/escalation-guide.md`).

**Phase defaults:** model Sonnet, effort medium, unless a card says otherwise. No card in this phase needs an Opus review: nothing here enforces an invariant at runtime — it builds the structures that later phases enforce them in. The architecture tests in P0-09 are the exception that guards the rest, so they are marked L and run on every later card.

| Card | Title | Size | Depends on |
|------|-------|------|-----------|
| P0-01 | Repository scaffold and tooling | M | — |
| P0-02 | Makefile, pre-commit and CI | S | P0-01 |
| P0-03 | Kernel domain: ids, enums, Money, errors | M | P0-01 |
| P0-04 | Kernel domain: cross-case socket models | S | P0-03 |
| P0-05 | Kernel ports | M | P0-03 |
| P0-06 | Disabled, Null and fake adapters | M | P0-05 |
| P0-07 | Module registry, `modules.yaml` and startup validation | L | P0-05, P0-06 |
| P0-08 | Composition root, `/healthz` and the API skeleton | M | P0-07 |
| P0-09 | Infrastructure: logging, Clock, IdGenerator | M | P0-03 |
| P0-10 | Architecture tests | L | P0-07, P0-08 |
| P0-11 | Contract-test skeletons | S | P0-05 |
| P0-12 | ADRs 0001-0003 and docs | S | P0-10 |

---

## P0-01 — Repository scaffold and tooling

**Files:** `pyproject.toml`, `.gitignore` (exists), `src/dispute_agent/__init__.py`, the package tree of `docs/ARCHITECTURE.md` section 3 with `__init__.py` in each package, `tests/{unit,contract,integration,e2e,architecture,evals}/`.

**Implements:** `ARCHITECTURE.md` section 3 (layout), `CLAUDE.md` section 7 (standards).

**Build:** `uv` project, Python 3.12+. Runtime dependencies: pydantic, pydantic-settings, fastapi, uvicorn, sqlalchemy, alembic, structlog, pyyaml, jinja2. Dev: pytest, hypothesis, ruff, mypy, import-linter, pre-commit. Configure `ruff` and `mypy --strict` in `pyproject.toml`; `mypy` strict applies to `src/`, tests may relax `disallow_untyped_defs`.

**Tests first:** `tests/architecture/test_layout.py` — every package named in `ARCHITECTURE.md` section 3 exists and is importable. This fails first and passes when the tree is created.

**Acceptance:** `make setup && make lint && make typecheck` clean on an empty tree.

**Size:** M. **Model:** Sonnet, effort medium.

**Watch:** the import-linter `root_package` is `dispute_agent`, so the distribution name and the package name must match what `ARCHITECTURE.md` section 2 declares. Getting this wrong surfaces only in P0-10, when the contracts silently match nothing.

---

## P0-02 — Makefile, pre-commit and CI

**Files:** `Makefile`, `.pre-commit-config.yaml`, `.github/workflows/verify.yml`.

**Implements:** `CLAUDE.md` section 15 (commands), section 14 (definition of done).

**Build:** the targets `setup, verify, test, test-integration, lint, typecheck, arch, run-api, run-approvals, demo, eval`. `verify` runs lint, typecheck, unit, contract, architecture and scenario tests, and **excludes** anything marked `integration` (`CLAUDE.md` section 8: no test touches the network by default). Targets that have nothing to run yet exit 0 with a message naming the phase that fills them, so `make verify` is green from the first commit. CI runs `make verify` on push and pull request.

**Tests first:** `tests/architecture/test_make_targets.py` — every target named in `CLAUDE.md` section 15 exists in the Makefile. Parse the Makefile; do not shell out to each target.

**Acceptance:** `make verify` exits 0.

**Size:** S. **Model:** Sonnet, effort low.

---

## P0-03 — Kernel domain: ids, enums, Money, errors

**Files:** `src/dispute_agent/kernel/domain/{ids,enums,money,errors}.py`.

**Implements:** `kernel-interfaces.md` sections 1-2 (ids, enums, `Money`), section 3 (`ReviewCode`), the error types named in `orchestrator.md` section 5 and `ARCHITECTURE.md` section 4.

**Build:** the `NewType` ids and their prefixes exactly as `kernel-interfaces.md` gives them (`evi_`, `tmr_`, `corr_`, …). `CaseState` with the 10 working states and 6 outcomes of `transitions.yaml` `states`. `Money` as `Decimal` in INR, integer paise in storage (ADR 0011) — never float. Typed errors: `ModuleUnavailable(module_name)`, `GuardrailBlocked`, `InvalidModelOutput`, `BudgetExceeded`, `AgentDrift`, `TransientPortError`, `PermanentPortError`, `AmbiguousOutcome`, `GuardFailed`. All domain models frozen.

**Tests first:**
- `tests/unit/domain/test_money.py` — paise round-tripping; no float anywhere in the path; addition of mismatched currency raises.
- `tests/unit/domain/test_enums.py` — `CaseState` membership matches `transitions.yaml` `states` exactly, read from the file, so the two can never drift.
- `tests/unit/domain/test_ids.py` — each id type rejects a value with the wrong prefix.

**Acceptance:** `pytest tests/unit/domain -q`.

**Size:** M. **Model:** Sonnet, effort medium.

**Watch:** `ReviewCode` has 20+ members (`kernel-interfaces.md` section 3) and later phases check them by name. Transcribe the whole enum now, including `OPS_TIMEOUT`, `DELIVERY_FAILED`, `LATE_INBOUND`, `STEP_LIMIT` and `CREDIT_REJECTED`, even though nothing raises them until phases 3-5.

---

## P0-04 — Kernel domain: cross-case socket models

**Files:** `src/dispute_agent/kernel/domain/patterns.py`.

**Implements:** `kernel-interfaces.md` (`PatternRecord`, `CautionSignal`, `CaseOutcome`), `FLOW.md` section 6, D3, I7.

**Build:** the three models, frozen. `PatternRecord.summary` is capped at 400 characters and is **untrusted when model-produced** (`agent-design.md` section 1) — the model carries the field, the briefing decides its trust label; nothing here may treat it as a verified fact. `CautionSignal` can only raise a verdict, never lower one: give it a `min_verdict` field and no way to express a maximum.

**Tests first:** `tests/unit/domain/test_patterns.py` — a `CautionSignal` cannot be constructed that lowers a verdict (the type makes it unrepresentable); `PatternRecord` rejects a summary over 400 characters.

**Acceptance:** `pytest tests/unit/domain/test_patterns.py -q`.

**Size:** S. **Model:** Sonnet, effort medium.

**Why this is in Phase 0 at all:** D3. The socket exists from the start so that connecting cross-case intelligence in Phase 7 changes zero lines in `kernel/`. A test in P0-10 asserts that.

---

## P0-05 — Kernel ports

**Files:** `src/dispute_agent/kernel/ports/` — one file per port.

**Implements:** `ARCHITECTURE.md` section 4 (the sketch), `kernel-interfaces.md` sections 4-6 (the authoritative signatures).

**Build:** every port as a `typing.Protocol`, runtime-checkable only where a test needs it. The full list: `Clock`, `IdGenerator`, `CaseRepository`, `EventLog`, `EvidenceSource`, `Reasoner`, `ReplyInterpreter`, `MessageDrafter`, `MessageChannel`, `TicketingSystem`, `Ledger`, `ApprovalService`, `PatternContextProvider`, `CaseOutcomeObserver`, `PromptRegistry`, `Tracer`, `ModelGateway`, `InputScreen`. Where `ARCHITECTURE.md` section 4 and `kernel-interfaces.md` differ, **`kernel-interfaces.md` wins** (`CLAUDE.md` section 18) — section 4 says its signatures are a sketch.

**Tests first:** `tests/architecture/test_ports.py` — every port named in `ARCHITECTURE.md` section 4 exists in `kernel/ports`; no port module imports anything outside the standard library, `pydantic` and `kernel.domain`.

**Acceptance:** `make arch && pytest tests/architecture/test_ports.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

**Watch:** ports take and return frozen domain models, never dicts. `ModelGateway.complete` is the only path to any model (I14) — no other port may accept a prompt or return model output.

---

## P0-06 — Disabled, Null and fake adapters

**Files:** `src/dispute_agent/modules/*/disabled.py` for every module port; `modules/cross_case/null/`; `modules/tracing/noop/`; `modules/input_screen/rules_based/`; `modules/model_gateway/fake/`.

**Implements:** `ARCHITECTURE.md` section 4 (last paragraph), `orchestrator.md` section 5, I6, I15.

**Build:**
- A `Disabled` implementation of every module port that raises `ModuleUnavailable(module_name)` from **every** method. No method may silently succeed.
- `NullPatternContextProvider.patterns_for` returns an empty sequence; `NullOutcomeObserver.on_outcome` does nothing. Both are selected whenever `cross_case` is disabled.
- `NoopTracer.span` is a no-op context manager.
- `RulesBasedInputScreen` — a stub returning no flags. Advisory only (I15): a clean result must never relax anything downstream, which is a property of the caller, asserted in P0-10.
- `FakeModelGateway` — canned responses keyed by prompt name, for tests only.

**Tests first:** `tests/unit/modules/test_disabled_adapters.py` — parametrised over every `Disabled` adapter and every method on its port: each raises `ModuleUnavailable`, and the exception names the module. This is the test that keeps I6 honest as ports grow.

**Acceptance:** `pytest tests/unit/modules -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P0-07 — Module registry, `modules.yaml` and startup validation

**Files:** `src/dispute_agent/kernel/registry/{module_spec,registry,validation}.py`, `config/modules.yaml`.

**Implements:** `ARCHITECTURE.md` section 5, `config.md` (the `modules.yaml` schema), `CLAUDE.md` section 4 ("startup validates dependencies and refuses to run an unsafe combination").

**Build:** a `ModuleSpec` declaring each module's name, its port, its available adapters and its dependencies. The registry resolves `modules.yaml` to concrete adapters. Validation **fails fast at startup** (`CLAUDE.md` section 6) on: an unknown module, an unknown adapter, a missing dependency, a required module disabled, and an unsafe combination — the named one being `ledger` enabled with `approvals` disabled (`ARCHITECTURE.md` section 5), because a credit could then be posted with no approval record to bind it to (I3).

**Tests first:** `tests/unit/registry/test_validation.py`, one failing case per rule above, each asserting the error names the offending module. Plus: the shipped `config/modules.yaml` validates.

**Acceptance:** `pytest tests/unit/registry -q`.

**Size:** L. **Model:** Sonnet, effort medium.

**Watch:** this card decides whether every later "module disabled" scenario behaves (S05b, S20, S41). Getting the dependency graph right here is worth more than speed.

---

## P0-08 — Composition root, `/healthz` and the API skeleton

**Files:** `src/dispute_agent/app.py`, `src/dispute_agent/api/{__init__,health}.py`.

**Implements:** `ARCHITECTURE.md` sections 2-3, `CLAUDE.md` section 6 (dependency injection, no global state).

**Build:** `app.py` is the **only** composition root: it reads configuration, resolves the registry and injects adapters through constructors. No module constructs another. An app factory (`create_app()`) so tests build an app with a different `modules.yaml` without environment variables. `/healthz` lists each module, its state (enabled/disabled) and the adapter in use.

**Tests first:**
- `tests/unit/api/test_healthz.py` — with a fixture `modules.yaml` that disables `email`, `/healthz` reports email disabled and names the `Disabled` adapter.
- `tests/architecture/test_composition_root.py` — nothing imports `app`.

**Acceptance:** `make run-api` starts; `curl localhost:8000/healthz` lists modules.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P0-09 — Infrastructure: logging, Clock, IdGenerator

**Files:** `src/dispute_agent/infra/{logging,clock,ids,settings}.py`.

**Implements:** `CLAUDE.md` section 11 (observability), `observability.md` (log fields and redaction), I12, I14.

**Build:** structlog JSON logging with `case_id`, `event_id` and `correlation_id` on every line, and a **redaction processor** that masks email addresses and personal names. `Clock` and `IdGenerator` each with a system implementation and a fake one, so tests are deterministic (`CLAUDE.md` section 6). Settings through pydantic-settings; `.env` is git-ignored and `.env.example` lists names only.

**Tests first:**
- `tests/unit/infra/test_redaction.py` — a log line containing an email address and a name emits neither; a sentinel secret value in settings never appears in any log line (I12).
- `tests/unit/infra/test_clock.py` — `FakeClock` advances only when told; two calls to `now()` without an advance return the same instant.

**Acceptance:** `pytest tests/unit/infra -q`.

**Size:** M. **Model:** Sonnet, effort medium.

**Watch:** the redaction processor is the first half of I12. The sentinel scan that proves it end to end is scenario S30, built in Phase 4 — write the processor so a scan can find nothing, not so a test passes.

---

## P0-10 — Architecture tests

**Files:** `pyproject.toml` (import-linter contracts), `tests/architecture/test_imports.py`, `test_no_inline_prompts.py`, `test_cross_case_socket.py`.

**Implements:** `ARCHITECTURE.md` section 2 (the three contracts, verbatim), `CLAUDE.md` sections 4, 8, 9, D3.

**Build:**
- The three import-linter contracts exactly as `ARCHITECTURE.md` section 2 gives them: "Kernel is pure", "Modules are independent", "Packs depend on kernel only". `make arch` runs them.
- A test that fails if prompt-like text appears inline in `src/` (`prompts/README.md`): runtime prompts live only in `prompts/`.
- A test that the cross-case socket is connectable without touching `kernel/`: swapping `NullPatternContextProvider` for a stub provider through configuration alone changes behaviour, and `kernel/` is byte-identical. This is D3 made testable in Phase 0 rather than discovered in Phase 7.

**Tests first:** the tests are the deliverable. Write each so it fails against a deliberate violation first — add a temporary `import sqlalchemy` to a kernel module, see the contract fail, remove it.

**Acceptance:** `make arch` passes; each test demonstrably fails when its rule is broken.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** not required, but if any contract has to be weakened to make it pass, stop and use `architect` — a weakened contract in Phase 0 is a hole every later phase builds over.

---

## P0-11 — Contract-test skeletons

**Files:** `tests/contract/test_<port>.py`, one per port.

**Implements:** `CLAUDE.md` section 8 ("one suite per port, run against every adapter").

**Build:** a skeleton per port, skipped with a reason naming the phase that fills it (`pytest.mark.skip(reason="filled in Phase 3")`). Each skeleton defines the fixture shape — a suite parametrised over adapters — so a later phase adds cases, not scaffolding.

**Tests first:** not applicable; these are tests.

**Acceptance:** `pytest tests/contract -q` reports all skipped, none failing, and every skip reason names a phase.

**Size:** S. **Model:** Sonnet, effort low.

---

## P0-12 — ADRs 0001-0003 and docs

**Files:** `docs/adr/0001-ports-and-adapters.md`, `0002-module-registry-and-disconnect-behaviour.md`, `0003-tooling.md`; update `docs/PROGRESS.md`; `docs/BACKLOG.md` (exists).

**Implements:** `CLAUDE.md` section 12.

**Build:** ADR numbers 0001-0003 are reserved for this phase (`PROGRESS.md`). Each: context, options, decision, consequences, decision and its "why" first. 0002 must state what happens when a module is disconnected — the `module_unavailable:<name>` behaviour (I6) — because that is the decision Phase 0 makes on behalf of every later phase.

**Tests first:** not applicable.

**Acceptance:** `docs/PROGRESS.md` lists 0001-0003; no TODO in the codebase without a `BACKLOG.md` entry (`CLAUDE.md` section 14).

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green; `/healthz` lists every module; `make arch` enforces all three contracts; every contract-test file exists and is skipped with a phase named; ADRs 0001-0003 written; `docs/PROGRESS.md` updated. Then `/phase-verify 0` and `/session-handoff`.

Phase 0 builds no behaviour. Its whole value is that the rules which later phases rely on — kernel purity, module independence, fail-fast configuration, the disabled-module path, the cross-case socket — are enforced by tests before there is any code to bend them.
