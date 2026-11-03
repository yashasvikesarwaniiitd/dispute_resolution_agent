"""The HTTP surface.

Routers are built from injected dependencies rather than reading a module-level app, so the
composition root stays the only place that knows how anything is assembled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from dispute_agent.api.cases import build_cases_router
from dispute_agent.api.events import build_events_router
from dispute_agent.api.health import build_health_router
from dispute_agent.api.timeline import build_timeline_router

if TYPE_CHECKING:
    from dispute_agent.api.runtime import Runtime
    from dispute_agent.kernel.registry.registry import Registry

__all__ = ["build_cases_router", "build_events_router", "build_health_router", "build_router"]


def build_router(registry: Registry, runtime: Runtime | None = None) -> APIRouter:
    """`/healthz` always; the case routes once there is storage to serve them from."""
    router = APIRouter()
    router.include_router(build_health_router(registry))
    if runtime is not None:
        router.include_router(build_events_router(runtime))
        router.include_router(build_cases_router(runtime))
        router.include_router(build_timeline_router(runtime))
    return router
