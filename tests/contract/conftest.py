"""The shape every contract suite shares (CLAUDE.md section 8).

One suite per port, run against every adapter of that port — fake and real. A new adapter is
done when it passes the suite. That rule is what stops the fake and the real thing drifting
apart, which is the failure that makes a green test run meaningless: every test passes
against a fake that is kinder than the system it stands for.

How a skeleton becomes a suite. Each file lists its adapters with `pending(...)` while they
are unwritten, and the phase that writes one changes that single entry to `ready(...)` with a
factory. Every test in the file then runs against it. Nothing else moves — no fixture to
write, no parametrisation to add — which is the point of building the scaffolding now and the
reason these files are worth having while they are empty.

Until then `pytest tests/contract` reports skips, each naming the phase that owes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(frozen=True)
class AdapterUnderTest:
    """One adapter a suite runs against."""

    name: str
    #: The phase that writes it. Named in the skip reason, so a skipped run reads as a
    #: schedule rather than as a pile of forgotten tests.
    phase: str
    #: None while the adapter does not exist. A phase fills this in and the suite wakes up.
    build: Callable[[], Any] | None = None
    #: True for an adapter that talks to a real service. CLAUDE.md section 8: no test touches
    #: the network by default, so these also carry the integration marker.
    integration: bool = False


def pending(name: str, phase: str, *, integration: bool = False) -> AdapterUnderTest:
    return AdapterUnderTest(name=name, phase=phase, integration=integration)


def ready(
    name: str, build: Callable[[], Any], *, phase: str = "", integration: bool = False
) -> AdapterUnderTest:
    return AdapterUnderTest(name=name, phase=phase, build=build, integration=integration)


def params(adapters: list[AdapterUnderTest]) -> list[Any]:
    """Turn the adapter list into pytest params, marking the real ones as integration."""
    out: list[Any] = []
    for adapter in adapters:
        marks = [pytest.mark.integration] if adapter.integration else []
        out.append(pytest.param(adapter, id=adapter.name, marks=marks))
    return out


def build_or_skip(adapter: AdapterUnderTest) -> Any:
    """The one place a skeleton turns into a skip, so every reason reads the same way."""
    if adapter.build is None:
        pytest.skip(f"{adapter.name} adapter is filled in {adapter.phase}")
    return adapter.build()
