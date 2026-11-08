"""The hosted operations console: Streamlit Community Cloud's entry point (ADR 0045).

One process holds both halves. The dispute service - the same FastAPI app `make run-api`
serves, with its background runner - is built once per process and driven through its own
route in memory (`POST /events`), so a ticket takes exactly the path it takes locally:
intake, the allowlist (I11), dedupe, the runner. The console's four screens sit on top.

Configuration comes from the host's secret store, which Streamlit also exposes as
environment variables: MODULES_FILE=modules.hosted.yaml (real Gmail and Jira), the account
settings and CONSOLE_PASSWORD (the gate; without it nothing opens, and past it the Review
screen asks only for the reviewer's name, ADR 0047). No secret lives in the repository. The case store and the simulated ledger are
created on first start and are lost when the host restarts the app; real email and Jira are
not.

Lives at the repository root, like the scripts, because it composes the app: nothing inside
`dispute_agent` imports `dispute_agent.app` (tests/architecture/test_composition_root.py).
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dispute_agent.ui.app import main


@st.cache_resource(show_spinner="Starting the dispute service")
def _service() -> Any:
    """Migrate the case store, build the app, start its runner. Once per process."""
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from dispute_agent.app import create_app

    command.upgrade(Config("alembic.ini"), "head")
    client = TestClient(create_app())
    client.__enter__()  # runs the lifespan: recovery, then a runner tick every few seconds
    return client


def _post(body: dict[str, str]) -> tuple[int, dict[str, Any]]:
    answer = _service().post("/events", json=body)
    parsed = answer.json() if answer.content else {}
    return answer.status_code, parsed if isinstance(parsed, dict) else {}


main(hosted=True, post=_post, start=_service)
