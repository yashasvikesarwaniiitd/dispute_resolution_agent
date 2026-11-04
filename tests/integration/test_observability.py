"""P6-12: traces reach Application Insights, and no message content goes with them (I14).

Opt-in (`@pytest.mark.integration`): needs `uv sync --group telemetry` and the owner's
`APPLICATIONINSIGHTS_CONNECTION_STRING` in .env. Never part of `make verify`.

A traced run of a whole case is exported to Application Insights. Its outside texts - the
dispute statement and the notes - carry a sentinel; an in-memory processor watches every span
that is exported, and the test fails if the sentinel, or any attribute off the allow-list,
appears in any of them. The flush must succeed: that is the spans reaching the service. The
owner then sees them in the portal (Application Insights > Search), with no content.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import pytest

SENTINEL = "SENTINEL-CONTENT-7f3a"

pytestmark = pytest.mark.integration


@pytest.fixture
def connection_string() -> str:
    from dispute_agent.infra.settings import Settings

    secret = Settings().applicationinsights_connection_string
    value = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING") or (
        secret.get_secret_value() if secret is not None else None
    )
    if not value:
        pytest.skip("APPLICATIONINSIGHTS_CONNECTION_STRING is not set (owner setup, P6-12)")
    pytest.importorskip("azure.monitor.opentelemetry.exporter")
    return str(value)


def test_a_traced_case_reaches_application_insights_with_no_content(
    connection_string: str,
) -> None:
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from dispute_agent.api.full_run import open_world
    from dispute_agent.infra.clock import FakeClock
    from dispute_agent.infra.ids import FakeIdGenerator
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.modules.tracing.otel import OtelTracer, allowed_for

    watched = InMemorySpanExporter()
    tracer = OtelTracer.to_azure_monitor(connection_string, SimpleSpanProcessor(watched))
    world = open_world(
        "calc_error_wrong_slab",
        policy=PolicyConfig(),
        clock=FakeClock(datetime(2026, 11, 4, 9, 0, tzinfo=UTC)),
        ids=FakeIdGenerator(),
        statement=f"My parcel was weighed wrong. {SENTINEL}",
        notes=(f"hub note {SENTINEL}",),
    )
    world.line.tracer = tracer
    try:
        with tracer.span("dispatch", case_id=str(world.case_id), event_type="DISPUTE_RAISED"):
            world.drive()
    finally:
        world.close()

    spans: list[Any] = list(watched.get_finished_spans())
    assert spans, "nothing was traced"
    for span in spans:
        attributes = dict(span.attributes or {})
        assert SENTINEL not in str(attributes), f"content in span {span.name}"
        assert set(attributes) <= allowed_for(span.name), f"off-list attribute in {span.name}"
    assert tracer.flush(), "the spans did not reach Application Insights"
