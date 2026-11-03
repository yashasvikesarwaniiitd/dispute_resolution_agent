"""The no-op tracer (kernel-interfaces.md section 6.1).

The default, and also what `tracing: {enabled: false}` wires in — orchestrator.md section 5:
"no-op tracer; behaviour unchanged". There is deliberately no DisabledTracer: the port's own
contract says a tracer never raises and a broken one degrades to a no-op, so an adapter that
raised would break the port it claims to implement. Observability is the one thing whose
absence must not change what the system decides.

CLAUDE.md section 11: OpenTelemetry is an adapter, added only if it earns its place.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

MODULE = "tracing"


class NoopTracer:
    """Implements Tracer."""

    @contextmanager
    def span(self, name: str, **attrs: str) -> Iterator[None]:
        yield
