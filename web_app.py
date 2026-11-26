"""The Dispute Desk web workspace's entry point (ADR 0068): `make run-web`.

One process holds the dispute service (the same FastAPI app `make run-api` serves, with its
background runner), the workspace API at `/api/ws`, and the built web app at `/app`. One
process, so exactly one runner owns the case store - never run this beside `make run-api`.

Lives at the repository root, like `streamlit_app.py`, because it composes the app with the
console: nothing inside `dispute_agent` imports `dispute_agent.app`, and nothing outside
`dispute_agent.ui` imports the console (tests/architecture).

    uv run uvicorn web_app:create --factory --host 127.0.0.1
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from dispute_agent.api.events import RaiseDispute, raise_dispute
from dispute_agent.app import _default_database, build_case_explainer, create_app
from dispute_agent.infra.settings import Settings
from dispute_agent.ui.web.router import build_workspace_router

if TYPE_CHECKING:
    from collections.abc import Callable

    from dispute_agent.api.runtime import Runtime

#: The web app, built by `make web` (npm run build in web/).
DIST = Path(__file__).resolve().parent / "web" / "dist"


def create(
    config_dir: Path | None = None, app_env: str | None = None, database: Path | None = None
) -> FastAPI:
    app = create_app(config_dir=config_dir, app_env=app_env, database=database)
    runtime = app.state.application.runtime
    if runtime is not None:
        path = database or _default_database()
        app.include_router(
            build_workspace_router(
                poster(runtime),
                explain=None
                if path is None
                else lambda case_id, question: build_case_explainer(
                    Settings(), path, only_case=case_id
                ).ask(question),
            )
        )
    if (DIST / "index.html").is_file():  # the API works without it; `make web` builds it
        app.mount("/app", StaticFiles(directory=DIST, html=True), name="web")
        app.add_api_route(
            "/", lambda: RedirectResponse("/app/"), include_in_schema=False, methods=["GET"]
        )
    return app


def poster(runtime: Runtime) -> Callable[[dict[str, str]], tuple[int, dict[str, Any]]]:
    """Raise a dispute exactly as `POST /events` does, in process, for the seller view."""

    def post(body: dict[str, str]) -> tuple[int, dict[str, Any]]:
        try:
            return 200, raise_dispute(runtime, RaiseDispute.model_validate(body)).model_dump()
        except HTTPException as refused:
            return refused.status_code, {}

    return post
