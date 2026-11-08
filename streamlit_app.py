"""The hosted operations console: Streamlit Community Cloud's entry point (ADR 0045).

One process holds both halves. The dispute service - the same FastAPI app `make run-api`
serves, with its background runner - is built once per loaded copy of the code and driven
through its own route in memory (`POST /events`), so a ticket takes exactly the path it takes
locally: intake, the allowlist (I11), dedupe, the runner. The console's four screens sit on
top.

**Why "per loaded copy of the code".** When any file under the app's folder changes - and a
push to GitHub changes them in place on Streamlit Cloud - Streamlit unloads every watched
module and imports fresh copies on the next run. A service kept from before would still hold
the old classes, and the domain models are strict, so the old intake refused facts built from
the new `CaseFacts` (a pydantic ValidationError on raising a ticket, seen live on 2026-11-08).
`Services` notices the new copy, stops the old service (its runner thread with it, so two
runners never share the store) and starts a new one, which recovers from the store as a
restart would.

Configuration comes from the host's secret store, which Streamlit also exposes as
environment variables: MODULES_FILE=modules.hosted.yaml (real Gmail and Jira), the account
settings and CONSOLE_PASSWORD (the gate; without it nothing opens, and past it the Review
screen offers the listed approvers instead of the token, ADR 0047). No secret lives in the repository. The
case store and the simulated ledger are created on first start and are lost when the host
restarts the app; real email and Jira are not.

Lives at the repository root, like the scripts, because it composes the app: nothing inside
`dispute_agent` imports `dispute_agent.app` (tests/architecture/test_composition_root.py).
"""

from __future__ import annotations

import importlib
import threading
from typing import TYPE_CHECKING, Any

import streamlit as st

if TYPE_CHECKING:
    from collections.abc import Callable


class Services:
    """One running dispute service for the copy of the code that is loaded now."""

    def __init__(self) -> None:
        self._lock = threading.Lock()  # several sessions can rerun at once
        self._code: object | None = None
        self._client: Any = None

    def get(self, code: object, start: Callable[[], Any]) -> Any:
        """The service for `code` (a module object: a reload makes a new one)."""
        with self._lock:
            if self._client is None or self._code is not code:
                if self._client is not None:
                    self._client.__exit__(None, None, None)  # stops the old runner first
                    self._client = None
                self._client = start()
                self._code = code
            return self._client


@st.cache_resource
def _services() -> Services:
    """Kept across reruns and across Streamlit's module reloads: this file's own source is
    the cache key, and it does not change when the package's files do."""
    return Services()


def _start() -> Any:
    """Migrate the case store, build the app, start its runner."""
    from alembic import command
    from alembic.config import Config
    from fastapi.testclient import TestClient

    from dispute_agent.app import create_app

    command.upgrade(Config("alembic.ini"), "head")
    client = TestClient(create_app())
    client.__enter__()  # runs the lifespan: recovery, then a runner tick every few seconds
    return client


def _service() -> Any:
    # The module the service's classes come from; a reload replaces it with a new object.
    code = importlib.import_module("dispute_agent.kernel.domain.case")
    with st.spinner("Starting the dispute service"):
        return _services().get(code, _start)


def _post(body: dict[str, str]) -> tuple[int, dict[str, Any]]:
    answer = _service().post("/events", json=body)
    parsed = answer.json() if answer.content else {}
    return answer.status_code, parsed if isinstance(parsed, dict) else {}


if __name__ == "__main__":  # Streamlit runs this file as __main__; a test may import it
    from dispute_agent.ui.app import main

    main(hosted=True, post=_post, start=_service)
