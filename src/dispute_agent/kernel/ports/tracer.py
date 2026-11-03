"""The Tracer port (kernel-interfaces.md section 6.1).

A no-op by default. OpenTelemetry is an adapter and only earns its place if it is asked for.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable


@runtime_checkable
class Tracer(Protocol):
    def span(self, name: str, **attrs: str) -> AbstractContextManager[None]:
        """No content attributes unless telemetry.capture_content is on (I14).

        Never raises: a broken tracer degrades to a no-op rather than failing a case.
        """
        ...
