# ADR 0003: Toolchain — uv, ruff, mypy --strict, import-linter, and one `make`

Status: accepted (owner, 2026-11-03)
Date: 2026-11-03

## Decision
Python 3.12 pinned by a `.python-version` file and managed by `uv`. `ruff` for lint and format, `mypy --strict` over `src` and `tests`, `pytest` with `hypothesis`, `import-linter` for the layering contracts. Every target lives in `scripts/make.py`; the `Makefile` and the Windows shim both delegate to it, so one implementation runs everywhere. `make verify` is lint, format check, typecheck, architecture contracts and tests, in that order.

## Why
In plain terms: the checks that matter should fail on the machine of whoever broke them, not later and somewhere else.

The ordering of `verify` is deliberate: lint and types are cheaper than tests and catch a different class of mistake, so they run first and a broken build says what is wrong in seconds. `mypy --strict` covers `tests` as well as `src`, which has already paid for itself — a drifted adapter signature fails typecheck rather than surfacing as a confusing assertion three phases later, and the contract suites use annotated bindings for exactly that.

One implementation behind `make` is the part worth arguing for. A `Makefile` with real recipes would work on the CI image and not on the owner's Windows machine, and the usual answer — document both — means two sets of commands drifting apart. A Python script is the one artefact that runs identically in both places, and it also lets an unbuilt target report "pending, Phase 3" and exit zero instead of failing a build for work nobody has started.

The Python version is pinned in a file rather than only in `requires-python`, which is a floor rather than a choice. This was not theoretical: with only the floor, a stale managed interpreter directory had `uv` rebuild the environment on 3.12's successor, where `pyyaml` had no wheel and tried to compile.

## Options considered
- **Poetry or pip-tools.** Both work. `uv` resolves and installs fast enough that `make setup` is not a coffee break, and it manages the interpreter as well as the dependencies, which removes a whole class of "works on my machine".
- **black plus flake8 plus isort.** The familiar set, three tools and three configurations. `ruff` does all three, fast, with one configuration block.
- **mypy without `--strict`, or over `src` only.** Rejected: the untyped corners are exactly where the mistakes hide, and tests are where a port's shape is asserted.
- **A `Makefile` with real recipes.** Rejected for the portability reason above.
- **Writing the layering rules as a custom test.** Rejected: `import-linter` already expresses them declaratively, and the contracts can then be compared with `ARCHITECTURE.md` field by field.

## Consequences
Easier: `make verify` is the whole definition of done (CLAUDE.md section 14), and it is one command on any machine. A new contributor runs `make setup` and has the same interpreter as everyone else.

Harder: `import-linter` needs `include_external_packages = true` for "Kernel is pure" to see external packages at all — a tooling flag that `ARCHITECTURE.md` does not mention, so `tests/architecture/test_imports.py` asserts it is the *only* addition. Two per-file lint ignores are documented where they sit: `N818` in `kernel/domain/errors.py`, because the frozen specification names those exceptions exactly and outranks the naming convention; and the type-checking import rules in `kernel/ports/`, because the architecture test resolves port annotations at runtime to catch a port naming a model nobody built.

Watch: this repository lives in a OneDrive-synced folder on at least one machine, which refuses hardlinks into its sync root and intermittently locks files `uv` is replacing. `[tool.uv] link-mode = "copy"` works around it. Moving the checkout outside OneDrive would be the real fix — a `.venv` is not something a file sync should be carrying.

## Sources
Project documents: `CLAUDE.md` sections 7, 8, 14 and 15; `docs/ARCHITECTURE.md` section 2. Tool behaviour was established by running the tools in this repository, not from memory.
