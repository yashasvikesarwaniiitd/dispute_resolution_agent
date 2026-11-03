"""`modules.yaml` as data, and the resolved view of it (config.md section 2).

`ModulesFile` is the file; `Registry` is what the rest of the system asks. The registry
implements `ModuleStatus`, so a step that wants to know whether `email` is on asks the same
small port in production and in a test, and nothing reads the YAML twice.

Nothing here constructs an adapter. The registry says which adapter a module should use; the
composition root builds it (P0-08).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.json_values import JsonValue
from dispute_agent.kernel.registry.module_spec import MODULE_SPECS, ModuleSpec


class ModuleConfig(BaseModel):
    """One row of `modules.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    adapter: str
    secondary_adapter: str | None = None
    settings: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("adapter", mode="before")
    @classmethod
    def _catch_the_yaml_trap(cls, value: object) -> object:
        """`adapter: null` is YAML's None, not the adapter called "null".

        cross_case's adapter is genuinely named "null", so an unquoted value there parses as
        None and the module silently loses its adapter. The error says what to type.
        """
        if value is None:
            raise ConfigError(
                'adapter is empty: an unquoted null in YAML parses as nothing. Write "null" '
                "in quotes if you meant the cross_case null adapter."
            )
        return value


class ModulesFile(BaseModel):
    """The whole of `config/modules.yaml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    modules: dict[str, ModuleConfig]


@dataclass(frozen=True)
class ResolvedModule:
    name: str
    enabled: bool
    #: The adapter to build when enabled; when disabled, the off adapter its spec names.
    adapter: str
    secondary_adapter: str | None
    settings: dict[str, JsonValue]
    spec: ModuleSpec


class Registry:
    """The validated, resolved module table. Implements ModuleStatus.

    Built only by `validate_modules`, which is the one place that can decide a combination is
    safe. Constructing one directly from unvalidated config would defeat the point.
    """

    def __init__(self, modules: dict[str, ResolvedModule]) -> None:
        self._modules = modules

    def is_enabled(self, module: str) -> bool:
        return self._require(module).enabled

    def adapter_name(self, module: str) -> str:
        return self._require(module).adapter

    def resolved(self, module: str) -> ResolvedModule:
        return self._require(module)

    def all(self) -> tuple[ResolvedModule, ...]:
        return tuple(self._modules[name] for name in sorted(self._modules))

    def enabled_names(self) -> tuple[str, ...]:
        return tuple(name for name in sorted(self._modules) if self._modules[name].enabled)

    def _require(self, module: str) -> ResolvedModule:
        try:
            return self._modules[module]
        except KeyError:
            raise ConfigError(
                f"unknown module {module!r}; known modules are {sorted(MODULE_SPECS)}"
            ) from None
