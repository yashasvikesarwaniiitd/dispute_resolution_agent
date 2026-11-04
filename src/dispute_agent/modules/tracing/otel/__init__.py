"""The OpenTelemetry tracer, with an attribute allow-list (P4b-10, observability.md 3, I12, I14).

Spans are useful only if they can never carry what must not leave the machine: message
content, personal data, secrets. So this adapter does not trust its callers. Each span name
has a fixed set of attribute names (observability.md section 3); any other attribute is
dropped before the span is created, and values are cut to a short length. Content cannot
reach a span here even by mistake - there is no attribute it could ride in on.

Console export by default. Exporting to Application Insights needs the Azure Monitor exporter
and `APPLICATIONINSIGHTS_CONNECTION_STRING`; that is optional, kept inside this adapter, and
never required by `make verify` (Foundry's own GenAI tracing is preview: not enabled).

The port's contract: a tracer never raises. A failure inside tracing degrades to a no-op.
"""

from __future__ import annotations

from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

MODULE = "tracing"
ADAPTER = "otel"

#: observability.md section 3. The only attributes a span of each kind may carry.
ALLOWED: dict[str, frozenset[str]] = {
    "dispatch": frozenset({"case_id", "event_type", "reaction", "correlation_id"}),
    "step": frozenset({"case_id", "state_from", "state_to", "trigger", "transition_id"}),
    "policy.evaluate": frozenset({"stage", "intent_type", "verdict", "rule_ids_fired"}),
    "executor": frozenset({"action_type", "action_id", "outcome", "attempts", "found_by_lookup"}),
    "http": frozenset({"http.status_code", "retry_after_s", "attempt", "server.address"}),
    "model.call": frozenset(
        {
            "purpose",
            "route",
            "deployment",
            "model_id",
            "prompt_name",
            "prompt_version",
            "prompt_hash",
            "input_tokens",
            "output_tokens",
            "latency_ms",
            "outcome",
            "failover_from",
        }
    ),
}
MAX_VALUE = 120


def allowed_for(name: str) -> frozenset[str]:
    for prefix, names in ALLOWED.items():
        if name == prefix or name.startswith(prefix + "."):
            return names
    return frozenset()


def filtered(name: str, attrs: dict[str, str]) -> dict[str, str]:
    keep = allowed_for(name)
    return {k: str(v)[:MAX_VALUE] for k, v in attrs.items() if k in keep}


class OtelTracer:
    """Implements Tracer."""

    def __init__(self, tracer: Any | None = None, provider: Any | None = None) -> None:
        if tracer is None:
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

            provider = TracerProvider()
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
            tracer = provider.get_tracer("dispute_agent")
        self._tracer = tracer
        self._provider = provider

    @classmethod
    def to_azure_monitor(cls, connection_string: str, *extra_processors: Any) -> OtelTracer:
        """Spans to Application Insights (P6-12, foundry.md section 10, I14).

        The exporter alone - `azure-monitor-opentelemetry-exporter`, a beta, installed only with
        `uv sync --group telemetry` - never the `azure-monitor-opentelemetry` distro, whose
        automatic HTTP instrumentation would export full URLs (mailbox, `$filter` with the key,
        JQL) outside the allow-list (risk F13). Only this adapter's own spans are exported, and
        they carry only allow-listed attributes. Offline storage is off, so no span copy is ever
        written to disk. `extra_processors` lets a test watch exactly what is exported.
        """
        from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(
                AzureMonitorTraceExporter(
                    connection_string=connection_string, disable_offline_storage=True
                )
            )
        )
        for processor in extra_processors:
            provider.add_span_processor(processor)
        return cls(provider.get_tracer("dispute_agent"), provider)

    def flush(self, timeout_ms: int = 30_000) -> bool:
        """Send what is buffered now (a short run would otherwise exit first). Never raises."""
        if self._provider is None:
            return True
        try:
            return bool(self._provider.force_flush(timeout_ms))
        except Exception:
            return False

    @contextmanager
    def span(self, name: str, **attrs: str) -> Iterator[None]:
        try:
            manager = self._tracer.start_as_current_span(name, attributes=filtered(name, attrs))
            manager.__enter__()
        except Exception:
            yield
            return
        try:
            yield
        finally:
            with suppress(Exception):
                manager.__exit__(None, None, None)


__all__ = ["ADAPTER", "ALLOWED", "MODULE", "OtelTracer", "filtered"]
