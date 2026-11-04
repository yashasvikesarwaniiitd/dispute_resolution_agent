"""Every model call goes through the spending guard (I14, ADR 0017).

`build_model_gateway` in `app.py` returns only the guarded gateway. That is only a guarantee
if nothing else in `src/` builds a reasoner or a provider gateway on its own - a second
construction site would be a path to a model with no cap and no log. So this checks where
the constructors are called, by parsing the source rather than by grep, and that the one
builder really does wrap what it builds.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from dispute_agent.app import build_model_gateway
from dispute_agent.infra.clock import SystemClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway
from dispute_agent.kernel.policy.config import PolicyConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "dispute_agent"

#: Constructors that make a path to a model, and the one file allowed to call each.
GUARDED = {
    "LlmReasoner": {"app.py"},
    "FoundryAgentServiceGateway": {"app.py"},
    "FakeModelGateway": {"app.py"},
    "AzureAgentServiceClient": {"app.py"},
    "AnthropicDirectGateway": {"app.py"},
    "FoundryOpenAiGateway": {"app.py"},
}


def calls_to(name: str) -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if called == name:
                    found.add(str(path.relative_to(SRC)).replace("\\", "/"))
    return found


@pytest.mark.parametrize("name", sorted(GUARDED))
def test_only_the_composition_root_builds_a_path_to_a_model(name: str) -> None:
    assert calls_to(name) <= GUARDED[name], f"{name} is constructed outside app.py"


def test_the_builder_returns_the_guarded_gateway(tmp_path: Path) -> None:
    gateway = build_model_gateway(
        "fake",
        settings=Settings(_env_file=None),
        policy=PolicyConfig(),
        clock=SystemClock(),
        ids=UlidIdGenerator(),
        log_path=tmp_path / "calls.sqlite3",
    )
    assert isinstance(gateway, BudgetedModelGateway)


def test_the_foundry_route_refuses_to_start_without_its_settings(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="FOUNDRY_PROJECT_ENDPOINT"):
        build_model_gateway(
            "foundry_agent_service",
            settings=Settings(_env_file=None),
            policy=PolicyConfig(),
            clock=SystemClock(),
            ids=UlidIdGenerator(),
            log_path=tmp_path / "calls.sqlite3",
        )


def test_the_foundry_route_refuses_to_start_without_a_credit_date(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        foundry_project_endpoint="https://x.services.ai.azure.com/api/projects/p",
        foundry_model_deployment="gpt-5-mini",
    )
    with pytest.raises(ConfigError, match="AZURE_CREDIT_EXPIRES_ON"):
        build_model_gateway(
            "foundry_agent_service",
            settings=settings,
            policy=PolicyConfig(),
            clock=SystemClock(),
            ids=UlidIdGenerator(),
            log_path=tmp_path / "calls.sqlite3",
        )
