"""Card P0-07: startup refuses an unsafe combination.

One failing case per rule, each asserting the message names the module at fault — a startup
error nobody can act on is barely better than no error.

The case that earns the card its "L" is `ledger` on with `approvals` off. That is I3 defeated
by configuration rather than by code: credits would have somewhere to post and nowhere to be
approved from, and no amount of care downstream closes a hole opened in a YAML file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.registry.module_spec import (
    MODULE_SPECS,
    AdapterSpec,
    ModuleSpec,
)
from dispute_agent.kernel.registry.registry import ModulesFile
from dispute_agent.kernel.registry.validation import validate_modules

ROOT = Path(__file__).resolve().parents[3]
SHIPPED = ROOT / "config" / "modules.yaml"

#: A minimal, valid file: every module present, nothing clever switched on.
BASE: dict[str, dict[str, Any]] = {
    "evidence": {"enabled": True, "adapter": "synthetic_shipments"},
    "model_gateway": {"enabled": False, "adapter": "fake"},
    "reasoning": {"enabled": True, "adapter": "rules_only"},
    "input_screen": {"enabled": True, "adapter": "rules_based"},
    "tracing": {"enabled": True, "adapter": "noop"},
    "email": {"enabled": False, "adapter": "fake"},
    "ticketing": {"enabled": False, "adapter": "fake"},
    "ledger": {"enabled": False, "adapter": "simulated"},
    "approvals": {"enabled": True, "adapter": "store_backed"},
    "cross_case": {"enabled": False, "adapter": "null"},
    "evals": {"enabled": True, "adapter": "default"},
}


def a_file(**changes: dict[str, Any]) -> ModulesFile:
    modules = {name: dict(row) for name, row in BASE.items()}
    for name, change in changes.items():
        modules.setdefault(name, {}).update(change)
    return ModulesFile.model_validate({"modules": modules})


class TestTheShippedFile:
    def test_it_parses_and_validates(self) -> None:
        """If this fails, the app does not start."""
        raw = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        registry = validate_modules(ModulesFile.model_validate(raw))
        assert set(registry.enabled_names()) == {
            "approvals",
            "evals",
            "evidence",
            "input_screen",
            "reasoning",
            "tracing",
        }

    def test_it_validates_under_ci_too(self) -> None:
        """make verify runs with APP_ENV=ci and must never need a preview adapter."""
        raw = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        validate_modules(ModulesFile.model_validate(raw), app_env="ci")

    def test_it_lists_every_module_the_catalogue_knows(self) -> None:
        raw = yaml.safe_load(SHIPPED.read_text(encoding="utf-8"))
        assert set(raw["modules"]) == set(MODULE_SPECS)


class TestUnsafeCombinations:
    def test_ledger_without_approvals_is_refused(self) -> None:
        """I3. The named unsafe combination (ARCHITECTURE.md section 5)."""
        unsafe = a_file(ledger={"enabled": True}, approvals={"enabled": False})
        with pytest.raises(ConfigError) as caught:
            validate_modules(unsafe)
        assert "ledger" in str(caught.value)
        assert "approvals" in str(caught.value)

    def test_ledger_with_approvals_is_fine(self) -> None:
        registry = validate_modules(a_file(ledger={"enabled": True}))
        assert registry.is_enabled("ledger")

    def test_both_off_is_fine(self) -> None:
        """A dependency only binds while the dependent module is on."""
        validate_modules(a_file(approvals={"enabled": False}))

    def test_llm_reasoning_without_a_gateway_is_refused(self) -> None:
        """I14: reasoning on the llm adapter has no other route to a model."""
        with pytest.raises(ConfigError, match="model_gateway"):
            validate_modules(a_file(reasoning={"adapter": "llm"}))

    def test_llm_reasoning_with_a_gateway_is_fine(self) -> None:
        registry = validate_modules(
            a_file(reasoning={"adapter": "llm"}, model_gateway={"enabled": True})
        )
        assert registry.adapter_name("reasoning") == "llm"

    def test_rules_only_reasoning_needs_no_gateway(self) -> None:
        """The dependency is the adapter's, not the module's."""
        validate_modules(a_file())

    def test_sql_cross_case_without_ticketing_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="ticketing"):
            validate_modules(a_file(cross_case={"enabled": True, "adapter": "sql_context"}))


class TestUnknownNames:
    def test_an_unknown_module_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="shipping_oracle"):
            validate_modules(a_file(shipping_oracle={"enabled": True, "adapter": "default"}))

    def test_an_unknown_adapter_is_refused(self) -> None:
        with pytest.raises(ConfigError, match="telepathy"):
            validate_modules(a_file(reasoning={"adapter": "telepathy"}))

    def test_a_missing_module_is_refused(self) -> None:
        """Silence is not "leave it at the default": what is on is written down."""
        modules = {name: dict(row) for name, row in BASE.items()}
        del modules["approvals"]
        with pytest.raises(ConfigError, match="approvals"):
            validate_modules(ModulesFile.model_validate({"modules": modules}))

    def test_an_unknown_key_in_a_row_is_refused(self) -> None:
        """extra='forbid': a typo is a startup error, not a silent no-op (config.md 1)."""
        with pytest.raises(Exception, match=r"enabledd|extra"):
            a_file(evidence={"enabledd": True})


class TestTheYamlNullTrap:
    def test_an_unquoted_null_adapter_is_caught(self) -> None:
        """cross_case's adapter really is called "null", so YAML eats it unquoted."""
        raw = yaml.safe_load("modules:\n  cross_case: {enabled: false, adapter: null}\n")
        with pytest.raises(ConfigError, match="quotes"):
            ModulesFile.model_validate(raw)

    def test_the_quoted_form_works(self) -> None:
        raw = yaml.safe_load('modules:\n  cross_case: {enabled: false, adapter: "null"}\n')
        parsed = ModulesFile.model_validate(raw)
        assert parsed.modules["cross_case"].adapter == "null"


class TestSecondaryAdapter:
    def test_only_anthropic_direct_may_be_the_failover(self) -> None:
        with pytest.raises(ConfigError, match="anthropic_direct"):
            validate_modules(
                a_file(
                    model_gateway={
                        "enabled": True,
                        "adapter": "fake",
                        "secondary_adapter": "foundry_openai",
                    }
                )
            )

    def test_the_failover_cannot_be_the_primary(self) -> None:
        """Failover onto the route that just failed is worse than none: it looks covered."""
        with pytest.raises(ConfigError, match="failed"):
            validate_modules(
                a_file(
                    model_gateway={
                        "enabled": True,
                        "adapter": "anthropic_direct",
                        "secondary_adapter": "anthropic_direct",
                    }
                )
            )

    def test_a_valid_pair_is_accepted(self) -> None:
        registry = validate_modules(
            a_file(
                model_gateway={
                    "enabled": True,
                    "adapter": "foundry_agent_service",
                    "secondary_adapter": "anthropic_direct",
                }
            )
        )
        assert registry.resolved("model_gateway").secondary_adapter == "anthropic_direct"


class TestPreviewAdapters:
    def test_preview_is_refused_in_ci(self) -> None:
        with pytest.raises(ConfigError, match="otel_foundry"):
            validate_modules(a_file(tracing={"adapter": "otel_foundry"}), app_env="ci")

    def test_preview_is_allowed_outside_ci(self) -> None:
        validate_modules(a_file(tracing={"adapter": "otel_foundry"}), app_env="dev")


class TestRequiredModules:
    """No real module is required today — S05b, S20 and S41 each run with one switched off."""

    def test_no_shipped_module_is_required(self) -> None:
        assert [name for name, spec in MODULE_SPECS.items() if spec.required] == []

    def test_a_required_module_cannot_be_disabled(self) -> None:
        """Tested against a synthetic catalogue, since the real one marks nothing required."""
        specs = {
            "evidence": ModuleSpec(
                name="evidence",
                port=object,
                adapters={"synthetic_shipments": AdapterSpec("synthetic_shipments")},
                required=True,
            )
        }
        file = ModulesFile.model_validate(
            {"modules": {"evidence": {"enabled": False, "adapter": "synthetic_shipments"}}}
        )
        with pytest.raises(ConfigError, match="required"):
            validate_modules(file, specs=specs)


class TestResolution:
    def test_a_disabled_module_resolves_to_its_disabled_adapter(self) -> None:
        """I6: switched off means the Disabled adapter, which raises rather than skipping."""
        registry = validate_modules(a_file())
        assert registry.adapter_name("email") == "disabled"
        assert registry.is_enabled("email") is False

    def test_cross_case_resolves_to_null_not_disabled(self) -> None:
        """orchestrator.md section 5: no patterns, no signal."""
        registry = validate_modules(a_file())
        assert registry.adapter_name("cross_case") == "null"

    def test_tracing_resolves_to_noop(self) -> None:
        """The Tracer port says a tracer never raises, so there is no Disabled form."""
        registry = validate_modules(a_file(tracing={"enabled": False}))
        assert registry.adapter_name("tracing") == "noop"

    def test_asking_about_an_unknown_module_raises(self) -> None:
        registry = validate_modules(a_file())
        with pytest.raises(ConfigError, match="shipping_oracle"):
            registry.is_enabled("shipping_oracle")


class TestEveryProblemIsReportedAtOnce:
    def test_two_faults_are_both_named(self) -> None:
        """Fixing a config file one error per run is a miserable way to spend an afternoon."""
        with pytest.raises(ConfigError) as caught:
            validate_modules(
                a_file(
                    ledger={"enabled": True},
                    approvals={"enabled": False},
                    reasoning={"adapter": "telepathy"},
                )
            )
        message = str(caught.value)
        assert "ledger" in message
        assert "telepathy" in message


class TestTheCatalogueMatchesTheSpec:
    def test_adapters_match_config_md(self) -> None:
        """config.md section 2 is the authority; this catches the two drifting apart."""
        table = (ROOT / "docs" / "spec" / "config.md").read_text(encoding="utf-8")
        section = table.split("| Module | Adapters |")[1].split("\n\n")[0]
        for line in section.splitlines():
            cells = [cell.strip() for cell in line.split("|")[1:-1]]
            if len(cells) < 2 or not cells[0].startswith("`"):
                continue
            module = cells[0].strip("`")
            named = {part.strip().strip("`") for part in cells[1].split(",") if part.strip()}
            assert module in MODULE_SPECS, (
                f"config.md names module {module!r}, the catalogue does not"
            )
            assert named == set(MODULE_SPECS[module].adapters), (
                f"adapters for {module!r} differ: config.md {sorted(named)}, "
                f"catalogue {sorted(MODULE_SPECS[module].adapters)}"
            )
