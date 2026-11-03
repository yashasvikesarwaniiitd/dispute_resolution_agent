"""Card P0-05: the ports exist, and they stay pure.

Two guards. The first reads ARCHITECTURE.md section 4 and checks every port it names is
really here, so the sketch and the code cannot drift apart silently. The second parses each
port module's imports and refuses anything outside the standard library, pydantic and
kernel.domain — import-linter already forbids a named list of packages, and this is the
complement: it fails on a dependency nobody thought to ban.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Protocol, get_type_hints

import pytest

from dispute_agent.kernel import ports

ROOT = Path(__file__).resolve().parents[2]
PORTS_DIR = ROOT / "src" / "dispute_agent" / "kernel" / "ports"
ARCHITECTURE = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")

ALLOWED_ROOTS = {"pydantic", "dispute_agent"}

PORT_MODULES = sorted(p for p in PORTS_DIR.glob("*.py") if p.name != "__init__.py")


def _ports_named_in_architecture() -> set[str]:
    section = ARCHITECTURE.split("## 4.")[1].split("\n## ")[0]
    return set(re.findall(r"^class (\w+)\(Protocol\)", section, flags=re.MULTILINE))


def test_every_port_in_the_architecture_sketch_exists() -> None:
    named = _ports_named_in_architecture()
    assert named, "parsed no ports out of ARCHITECTURE.md section 4; the test is broken"
    missing = {name for name in named if not hasattr(ports, name)}
    assert not missing, f"ports named in ARCHITECTURE.md section 4 but absent: {sorted(missing)}"


def test_the_card_list_is_complete() -> None:
    """The 18 the P0-05 card names, plus KillSwitch and ModuleStatus from section 6.1."""
    expected = {
        "ApprovalService",
        "CaseOutcomeObserver",
        "CaseRepository",
        "Clock",
        "EventLog",
        "EvidenceSource",
        "IdGenerator",
        "InputScreen",
        "KillSwitch",
        "Ledger",
        "MessageChannel",
        "MessageDrafter",
        "ModelGateway",
        "ModuleStatus",
        "PatternContextProvider",
        "PromptRegistry",
        "Reasoner",
        "ReplyInterpreter",
        "TicketingSystem",
        "Tracer",
    }
    assert set(ports.__all__) == expected


@pytest.mark.parametrize("path", PORT_MODULES, ids=lambda p: p.name)
def test_a_port_imports_nothing_it_should_not(path: Path) -> None:
    """Standard library, pydantic and kernel.domain only — never a module, pack or adapter."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            roots = [(node.module or "").split(".")[0]]
        else:
            continue
        for root in roots:
            if root in ALLOWED_ROOTS or root == "":
                continue
            # Anything else must be a standard-library module.
            assert root in _STDLIB, f"{path.name} imports {root!r}, which is not in the kernel"


@pytest.mark.parametrize("path", PORT_MODULES, ids=lambda p: p.name)
def test_a_port_only_reaches_into_kernel_domain(path: Path) -> None:
    """Within dispute_agent, a port may see domain models and nothing else."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith(
            "dispute_agent"
        ):
            continue
        assert node.module is not None
        assert node.module.startswith("dispute_agent.kernel.domain"), (
            f"{path.name} imports {node.module}; a port may only use kernel.domain"
        )


@pytest.mark.parametrize("name", sorted(ports.__all__))
def test_every_exported_port_is_a_protocol(name: str) -> None:
    port = getattr(ports, name)
    assert issubclass(port, Protocol), f"{name} is not a Protocol"
    assert getattr(port, "_is_protocol", False), f"{name} is not a Protocol"


@pytest.mark.parametrize("name", sorted(ports.__all__))
def test_a_port_signature_resolves(name: str) -> None:
    """Catches a forward reference to a model that was never built.

    Without this, a port can name a type that does not exist and nothing notices until
    something tries to use it, three phases later.
    """
    port = getattr(ports, name)
    for attr in vars(port).values():
        if callable(attr) and getattr(attr, "__annotations__", None):
            get_type_hints(attr)


def test_the_reasoner_cannot_reach_a_write_port() -> None:
    """I1, read off the type rather than asserted about behaviour.

    `propose` takes a briefing and returns a proposal. If a store, a channel or an executor
    ever appears in that signature, the reasoner has been handed something it can write with.
    """
    hints = get_type_hints(ports.Reasoner.propose)
    assert set(hints) == {"briefing", "return"}
    assert hints["briefing"].__name__ == "CaseBriefing"
    assert hints["return"].__name__ == "Proposal"


def test_only_the_model_gateway_speaks_to_a_model() -> None:
    """I14: no other port accepts a prompt or returns model output."""
    model_types = {"ModelRequest", "ModelResponse", "RenderedPrompt"}
    for name in ports.__all__:
        if name in ("ModelGateway", "PromptRegistry"):
            continue
        port = getattr(ports, name)
        for attr in vars(port).values():
            if not (callable(attr) and getattr(attr, "__annotations__", None)):
                continue
            used = {getattr(t, "__name__", "") for t in get_type_hints(attr).values()}
            assert not (used & model_types), f"{name} handles model types: {used & model_types}"


_STDLIB = frozenset(
    {
        "__future__",
        "abc",
        "ast",
        "collections",
        "contextlib",
        "datetime",
        "decimal",
        "enum",
        "hashlib",
        "json",
        "pathlib",
        "re",
        "typing",
    }
)
