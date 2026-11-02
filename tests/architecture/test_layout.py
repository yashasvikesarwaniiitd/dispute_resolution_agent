"""Card P0-01: every package named in docs/ARCHITECTURE.md section 3 exists and imports.

The layout is frozen at arch-v1. A missing package here means either the tree is wrong
or the architecture document changed without an ADR.
"""

from __future__ import annotations

import importlib

import pytest

KERNEL_PACKAGES = [
    "dispute_agent.kernel",
    "dispute_agent.kernel.domain",
    "dispute_agent.kernel.statemachine",
    "dispute_agent.kernel.policy",
    "dispute_agent.kernel.policy.rules",
    "dispute_agent.kernel.orchestrator",
    "dispute_agent.kernel.executor",
    "dispute_agent.kernel.ports",
    "dispute_agent.kernel.registry",
]

MODULE_PACKAGES = [
    "dispute_agent.modules.model_gateway",
    "dispute_agent.modules.reasoning",
    "dispute_agent.modules.input_screen",
    "dispute_agent.modules.tracing",
    "dispute_agent.modules.email",
    "dispute_agent.modules.ticketing",
    "dispute_agent.modules.ledger",
    "dispute_agent.modules.approvals",
    "dispute_agent.modules.evidence",
    "dispute_agent.modules.cross_case",
    "dispute_agent.modules.evals",
]

OTHER_PACKAGES = [
    "dispute_agent.packs",
    "dispute_agent.packs.weight_dispute",
    "dispute_agent.infra",
    "dispute_agent.infra.sqlite",
    "dispute_agent.api",
]


@pytest.mark.parametrize("name", KERNEL_PACKAGES + MODULE_PACKAGES + OTHER_PACKAGES)
def test_package_exists(name: str) -> None:
    importlib.import_module(name)


def test_eleven_modules() -> None:
    """ARCHITECTURE section 2 lists exactly these eleven modules in the independence contract."""
    assert len(MODULE_PACKAGES) == 11
