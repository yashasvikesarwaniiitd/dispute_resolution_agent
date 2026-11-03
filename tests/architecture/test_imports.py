"""Card P0-10: the dependency rule, and the contracts that enforce it.

`make arch` runs import-linter and fails on a violation. This file guards the thing
import-linter cannot: the contracts themselves. A contract is only worth having if nobody
quietly edits it when it starts failing, and the card is explicit that a weakened contract in
Phase 0 is a hole every later phase builds over.

So the contracts are read out of ARCHITECTURE.md section 2 and compared with the ones in
pyproject.toml, field by field. Deleting a forbidden module to make a build pass fails here.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = ROOT / "pyproject.toml"
ARCHITECTURE = ROOT / "docs" / "ARCHITECTURE.md"

#: The one key pyproject carries that the spec block does not. "Kernel is pure" forbids
#: external packages, and import-linter only sees them with this on. A tooling flag, not a
#: change to any contract — the per-contract comparison below is what proves that.
PERMITTED_EXTRA_KEYS = {"include_external_packages"}


def spec_block() -> dict[str, Any]:
    """The import-linter TOML fenced in ARCHITECTURE.md section 2."""
    section = ARCHITECTURE.read_text(encoding="utf-8").split("## 2.")[1].split("\n## ")[0]
    blocks = [part.split("```")[0] for part in section.split("```toml")[1:]]
    assert blocks, "found no TOML block in ARCHITECTURE.md section 2; this test is broken"
    block: dict[str, Any] = tomllib.loads("\n".join(blocks))["tool"]["importlinter"]
    return block


def shipped_block() -> dict[str, Any]:
    block: dict[str, Any] = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"][
        "importlinter"
    ]
    return block


def contracts_by_name(block: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {contract["name"]: contract for contract in block["contracts"]}


def test_the_same_three_contracts_exist() -> None:
    assert set(contracts_by_name(shipped_block())) == set(contracts_by_name(spec_block()))
    assert set(contracts_by_name(spec_block())) == {
        "Kernel is pure",
        "Modules are independent",
        "Packs depend on kernel only",
    }


@pytest.mark.parametrize(
    "name",
    ["Kernel is pure", "Modules are independent", "Packs depend on kernel only"],
)
def test_a_contract_matches_the_spec_exactly(name: str) -> None:
    """Field by field, so removing one forbidden module is as loud as deleting the contract."""
    assert contracts_by_name(shipped_block())[name] == contracts_by_name(spec_block())[name]


def test_the_root_package_matches() -> None:
    assert shipped_block()["root_package"] == spec_block()["root_package"]


def test_only_the_documented_tooling_flag_was_added() -> None:
    """Any other extra key would be a change to how the contracts are evaluated."""
    extra = set(shipped_block()) - set(spec_block())
    assert extra <= PERMITTED_EXTRA_KEYS, f"undocumented import-linter settings: {sorted(extra)}"


def test_kernel_purity_still_forbids_every_library_it_should() -> None:
    """Spelled out rather than inferred: these are the ones that would really get imported."""
    forbidden = set(contracts_by_name(shipped_block())["Kernel is pure"]["forbidden_modules"])
    for library in ("sqlalchemy", "httpx", "requests", "msal", "anthropic", "openai", "fastapi"):
        assert library in forbidden, f"the kernel may now import {library}"
    for layer in ("modules", "packs", "infra", "api", "app"):
        assert f"dispute_agent.{layer}" in forbidden, f"the kernel may now import {layer}"


def test_every_module_is_in_the_independence_contract() -> None:
    """A module added without a line here would be free to import its neighbours."""
    from dispute_agent.kernel.registry.module_spec import MODULE_SPECS

    listed = contracts_by_name(shipped_block())["Modules are independent"]["modules"]
    assert {name.rsplit(".", 1)[-1] for name in listed} == set(MODULE_SPECS)
