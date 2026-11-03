"""Startup validation: refuse to run an unsafe combination (CLAUDE.md sections 4 and 6).

Fail fast at startup, fail safe at runtime. These checks are the "fail fast" half: a
combination that could do damage stops the app with a message naming the modules, rather than
being discovered on a live case at three in the morning.

Every error is collected before any is raised. Someone editing `modules.yaml` should be told
everything that is wrong with it, not made to fix one line and run again.

The check that matters most is `ledger` enabled with `approvals` disabled. Credits would then
have somewhere to go and nowhere to be approved from, which is I3 defeated by configuration
rather than by code — the kind of hole no amount of careful programming downstream can close.
"""

from __future__ import annotations

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.registry.module_spec import (
    MODULE_SPECS,
    PERMITTED_SECONDARY_ADAPTER,
    ModuleSpec,
)
from dispute_agent.kernel.registry.registry import (
    ModuleConfig,
    ModulesFile,
    Registry,
    ResolvedModule,
)

#: In CI an adapter marked preview must not be reachable, so `make verify` never depends on
#: something unfinished (config.md section 2).
CI_ENV = "ci"


def validate_modules(
    file: ModulesFile,
    app_env: str = "dev",
    specs: dict[str, ModuleSpec] | None = None,
) -> Registry:
    """Resolve `modules.yaml` into a Registry, or raise ConfigError naming what is wrong.

    `specs` is injectable so a test can build a table with a required module in it; the
    default is the real catalogue.
    """
    catalogue = MODULE_SPECS if specs is None else specs
    problems: list[str] = []

    problems += _unknown_modules(file, catalogue)
    problems += _missing_modules(file, catalogue)
    problems += _unknown_adapters(file, catalogue)
    problems += _required_but_disabled(file, catalogue)
    problems += _secondary_adapters(file, catalogue)
    problems += _preview_in_ci(file, catalogue, app_env)
    problems += _missing_dependencies(file, catalogue)

    if problems:
        raise ConfigError(
            "config/modules.yaml cannot be used:\n  - " + "\n  - ".join(sorted(problems))
        )

    return Registry(
        {name: _resolve(name, row, catalogue[name]) for name, row in file.modules.items()}
    )


def _resolve(name: str, row: ModuleConfig, spec: ModuleSpec) -> ResolvedModule:
    return ResolvedModule(
        name=name,
        enabled=row.enabled,
        adapter=row.adapter if row.enabled else spec.off_adapter,
        secondary_adapter=row.secondary_adapter if row.enabled else None,
        settings=dict(row.settings),
        spec=spec,
    )


def _unknown_modules(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    return [
        f"unknown module {name!r} (known: {sorted(catalogue)})"
        for name in file.modules
        if name not in catalogue
    ]


def _missing_modules(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    """Every module in the catalogue must appear.

    An absent row is not "leave it at the default": the whole point of this file is that what
    is switched on is written down, so a module nobody mentioned is a question, not an answer.
    """
    return [
        f"module {name!r} is missing from the file"
        for name in catalogue
        if name not in file.modules
    ]


def _unknown_adapters(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    problems: list[str] = []
    for name, row in file.modules.items():
        spec = catalogue.get(name)
        if spec is None:
            continue
        if spec.adapter(row.adapter) is None:
            problems.append(
                f"module {name!r} names unknown adapter {row.adapter!r} "
                f"(known: {sorted(spec.adapters)})"
            )
    return problems


def _required_but_disabled(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    return [
        f"module {name!r} is required and cannot be disabled"
        for name, row in file.modules.items()
        if not row.enabled and (spec := catalogue.get(name)) is not None and spec.required
    ]


def _secondary_adapters(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    """The failover route may only be anthropic_direct, and never the primary (config.md 2).

    Equal to the primary would make failover a no-op that looks like resilience, which is
    worse than having none: F01 would retry the route that just failed and report it as a
    covered failure.
    """
    problems: list[str] = []
    for name, row in file.modules.items():
        secondary = row.secondary_adapter
        if secondary is None:
            continue
        spec = catalogue.get(name)
        if spec is None:
            continue
        if spec.adapter(secondary) is None:
            problems.append(f"module {name!r} names unknown secondary adapter {secondary!r}")
            continue
        if secondary != PERMITTED_SECONDARY_ADAPTER:
            problems.append(
                f"module {name!r} secondary adapter must be "
                f"{PERMITTED_SECONDARY_ADAPTER!r}, not {secondary!r}"
            )
        if secondary == row.adapter:
            problems.append(
                f"module {name!r} uses {secondary!r} as both primary and failover route, "
                "so failover would retry the route that just failed"
            )
    return problems


def _preview_in_ci(file: ModulesFile, catalogue: dict[str, ModuleSpec], app_env: str) -> list[str]:
    if app_env != CI_ENV:
        return []
    problems: list[str] = []
    for name, row in file.modules.items():
        spec = catalogue.get(name)
        if spec is None or not row.enabled:
            continue
        adapter = spec.adapter(row.adapter)
        if adapter is not None and adapter.preview:
            problems.append(
                f"module {name!r} uses preview adapter {row.adapter!r}, which APP_ENV=ci forbids"
            )
    return problems


def _missing_dependencies(file: ModulesFile, catalogue: dict[str, ModuleSpec]) -> list[str]:
    """A dependency is a property of the adapter in use, not of the module.

    `reasoning` needs `model_gateway` when it runs `llm` and needs nothing when it runs
    `rules_only`, so asking the module alone would be the wrong question.
    """
    problems: list[str] = []
    for name, row in file.modules.items():
        spec = catalogue.get(name)
        if spec is None or not row.enabled:
            continue
        adapter = spec.adapter(row.adapter)
        if adapter is None:
            continue
        for needed in adapter.depends_on:
            dependency = file.modules.get(needed)
            if dependency is None:
                problems.append(
                    f"module {name!r} (adapter {row.adapter!r}) depends on {needed!r}, "
                    "which is not in the file"
                )
            elif not dependency.enabled:
                problems.append(
                    f"module {name!r} (adapter {row.adapter!r}) depends on {needed!r}, "
                    "which is disabled"
                )
    return problems
