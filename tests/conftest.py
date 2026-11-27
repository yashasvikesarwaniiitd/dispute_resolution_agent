"""Shared fixtures."""

from __future__ import annotations

import pytest


@pytest.fixture
def approved_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The frozen environment (ADR 0072): only the three approved demo sellers are loaded, for a
    test whose assertions are about the approved data and must not move with generated sellers."""
    monkeypatch.setattr(
        "dispute_agent.modules.evidence.synthetic_shipments.catalogue.MODE", "approved"
    )
