"""The HTTP surface.

Routers are built from injected dependencies rather than reading a module-level app, so the
composition root stays the only place that knows how anything is assembled. Phase 1 adds the
event and case routes here (card P1-11).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from dispute_agent.api.health import build_health_router

if TYPE_CHECKING:
    from dispute_agent.kernel.registry.registry import Registry

__all__ = ["build_health_router", "build_router"]


def build_router(registry: Registry) -> APIRouter:
    router = APIRouter()
    router.include_router(build_health_router(registry))
    return router
