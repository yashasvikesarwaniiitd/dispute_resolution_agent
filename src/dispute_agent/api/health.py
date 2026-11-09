"""`/healthz` — what is switched on, and what is running it.

The first question anyone asks of a running system is "is it the configuration I think it
is?", and with twelve modules each having a disabled form, guessing is easy. This answers it
without a log dive: every module, whether it is on, and the adapter actually in use — which
for a disabled module is its Disabled adapter, not the one named in the file.

It reports no secrets and no settings (I12). Which adapter is running is operational fact;
what it was configured with may not be.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from pydantic import BaseModel

if TYPE_CHECKING:
    from dispute_agent.kernel.registry.registry import Registry


class ModuleHealth(BaseModel):
    name: str
    enabled: bool
    adapter: str


class Health(BaseModel):
    status: str
    modules: list[ModuleHealth]


def build_health_router(registry: Registry) -> APIRouter:
    """The registry is injected, not imported from a global (CLAUDE.md section 6)."""
    router = APIRouter()

    @router.get("/healthz", response_model=Health)
    def healthz() -> Health:
        return Health(
            status="ok",
            modules=[
                ModuleHealth(name=m.name, enabled=m.enabled, adapter=m.adapter)
                for m in registry.all()
            ],
        )

    return router
