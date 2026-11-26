"""Streamlit Community Cloud's entry point: the Dispute Desk (ADR 0045, ADR 0068).

`streamlit run streamlit_app.py` finds `app` below - an `st.App` (Streamlit 1.66) - and serves
it with uvicorn: Streamlit itself, plus two routes of our own beside it:

    /api/ws   the workspace API (src/dispute_agent/ui/web) over the dispute service
    /desk/    the built web app (web/dist, committed: the host has no Node to build it)

The Streamlit page, `desk_page.py`, fills the window with /desk/, so the site opens on the
Dispute Desk. Every path in the web app is relative, because the host serves the whole site
under a /~/+/ prefix.

The dispute service - the same FastAPI app `make run-api` serves, with its background runner -
is built once when the server starts (`lifespan`) and stops with it: one process, one runner.
Configuration comes from the host's secret store, which Streamlit also exposes as environment
variables: MODULES_FILE=modules.hosted.yaml (real Gmail and Jira) and the account settings.
No secret lives in the repository. The case store and the simulated ledger are created on
first start and are lost when the host restarts the app; real email and Jira are not.

**After a push, reboot the app** (Manage app, the menu, Reboot app). Streamlit reloads changed
modules in place, and a service kept across that once held the old domain classes while new
code built new ones (a pydantic ValidationError on raising a ticket, seen live on 2026-11-08).
The service here is built once from the code loaded at start, so the file watcher is off and
a reboot is what loads new code.

Lives at the repository root because it composes the app with the console: nothing inside
`dispute_agent` imports `dispute_agent.app` (tests/architecture/test_composition_root.py).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import streamlit as st
from fastapi import FastAPI
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from streamlit import config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "web" / "dist"

#: The workspace API. Its routes are added when the service starts; routing is per request.
DESK_API = FastAPI(
    title="Dispute Desk workspace API", docs_url=None, redoc_url=None, openapi_url=None
)

# One copy of the code per process: no in-place reload after a push (see the module notes).
config.set_option("server.fileWatcherType", "none")


def start() -> FastAPI:
    """Migrate the case store, build the dispute service, and give the desk its API."""
    from alembic import command
    from alembic.config import Config
    from web_app import poster

    from dispute_agent.app import build_case_explainer, create_app
    from dispute_agent.infra.settings import Settings
    from dispute_agent.ui.data import CASES
    from dispute_agent.ui.web.router import build_workspace_router

    migrations = Config(str(ROOT / "alembic.ini"))
    migrations.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(migrations, "head")
    service = create_app()
    DESK_API.include_router(
        build_workspace_router(
            poster(service.state.application.runtime),
            explain=lambda case_id, question: build_case_explainer(
                Settings(), CASES, only_case=case_id
            ).ask(question),
            prefix="",
            # shortcut: the site password is off by the owner's choice (2026-11-25), as on the
            # Streamlit console before it: anyone with the link can approve as a listed
            # approver. To close it, pass open_site=False and set CONSOLE_PASSWORD.
            open_site=True,
        )
    )
    return service


@asynccontextmanager
async def lifespan(_app: Any) -> AsyncIterator[None]:
    import anyio

    service = await anyio.to_thread.run_sync(start)
    async with service.router.lifespan_context(service):  # recovery, then the runner's ticks
        yield


app = st.App(
    "desk_page.py",
    routes=[
        Mount("/api/ws", app=DESK_API),
        Mount("/desk", app=StaticFiles(directory=DIST, html=True)),
    ],
    lifespan=lifespan,
)
