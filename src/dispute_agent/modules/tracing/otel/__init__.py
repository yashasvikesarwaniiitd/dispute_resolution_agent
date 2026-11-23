"""The OpenTelemetry tracer, with an attribute allow-list (P4b-10, observability.md 3, I12, I14).

Spans are useful only if they can never carry what must not leave the machine: message
content, personal data, secrets. So this adapter does not trust its callers. Each span name
has a fixed set of attribute names (observability.md section 3); any other attribute is
dropped before the span is created, and values are cut to a short length. Content cannot
reach a span here even by mistake - there is no attribute it could ride in on.

Console export by default. Exporting to Application Insights needs the Azure Monitor exporter
and `APPLICATIONINSIGHTS_CONNECTION_STRING`; that is optional, kept inside this adapter, and
never required by `make verify` (Foundry's own GenAI tracing is preview: not enabled).

Langfuse (ADR 0057) takes the same spans over OTLP/HTTP. Its own field names are derived from
attributes already on the allow-list - an AI call becomes a "generation" with its model, tokens
and cost, and a case id becomes the Langfuse session - so nothing new can leave the machine.

The port's contract: a tracer never raises. A failure inside tracing degrades to a no-op.
"""

from __future__ import annotations

import base64
import json
import time
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
            "case_id",
            "cost_usd",
        }
    ),
}
MAX_VALUE = 120
#: Langfuse Cloud, EU region. The US region is https://us.cloud.langfuse.com (LANGFUSE_HOST).
LANGFUSE_CLOUD = "https://cloud.langfuse.com"


def allowed_for(name: str) -> frozenset[str]:
    for prefix, names in ALLOWED.items():
        if name == prefix or name.startswith(prefix + "."):
            return names
    return frozenset()


def filtered(name: str, attrs: dict[str, str]) -> dict[str, str]:
    keep = allowed_for(name)
    return {k: str(v)[:MAX_VALUE] for k, v in attrs.items() if k in keep}


def _number(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def langfuse_fields(name: str, kept: dict[str, str]) -> dict[str, str]:
    """Langfuse's names for what a span already carries (ADR 0057). Derived only from `kept`,
    the allow-listed attributes, so no content or secret can arrive this way either."""
    fields: dict[str, str] = {}
    if kept.get("case_id"):
        fields["langfuse.session.id"] = kept["case_id"]
    if name != "model.call":
        return fields
    fields["langfuse.observation.type"] = "generation"
    if kept.get("model_id") not in (None, "", "unknown", "none"):
        fields["langfuse.observation.model.name"] = kept["model_id"]
    tokens = {k: _number(kept.get(f"{k}_tokens")) for k in ("input", "output")}
    if all(v is not None for v in tokens.values()):
        fields["langfuse.observation.usage_details"] = json.dumps(
            {k: int(v or 0) for k, v in tokens.items()}
        )
    cost = _number(kept.get("cost_usd"))
    if cost is not None:
        fields["langfuse.observation.cost_details"] = json.dumps({"total": cost})
    return fields


def langfuse_endpoint(host: str, public_key: str, secret_key: str) -> tuple[str, dict[str, str]]:
    """Langfuse's OTLP/HTTP traces URL and headers: Basic auth on `public:secret`, and the
    ingestion version without which spans can take up to 10 minutes to show."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")
    headers = {"Authorization": f"Basic {token}", "x-langfuse-ingestion-version": "4"}
    return host.rstrip("/") + "/api/public/otel/v1/traces", headers


def _start_ns(name: str, kept: dict[str, str]) -> int | None:
    """A model call's span is opened after the call returns, so it starts `latency_ms` back:
    otherwise every AI call would show a latency of zero."""
    latency = _number(kept.get("latency_ms")) if name == "model.call" else None
    return None if latency is None else time.time_ns() - int(latency * 1_000_000)


class OtelTracer:
    """Implements Tracer."""

    def __init__(
        self, tracer: Any | None = None, provider: Any | None = None, *, langfuse: bool = False
    ) -> None:
        self._langfuse = langfuse
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

    @classmethod
    def to_langfuse(
        cls, host: str, public_key: str, secret_key: str, *extra_processors: Any
    ) -> OtelTracer:
        """Spans to Langfuse over OTLP/HTTP (ADR 0057). Only this adapter's own spans, with
        allow-listed attributes and the Langfuse names derived from them; batched in the
        background, so a slow or unreachable Langfuse never delays a case."""
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint, headers = langfuse_endpoint(host, public_key, secret_key)
        provider = TracerProvider(resource=Resource.create({"service.name": "dispute_agent"}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers, timeout=10))
        )
        for processor in extra_processors:
            provider.add_span_processor(processor)
        return cls(provider.get_tracer("dispute_agent"), provider, langfuse=True)

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
            kept = filtered(name, attrs)
            extra = langfuse_fields(name, kept) if self._langfuse else {}
            manager = self._tracer.start_as_current_span(
                name, attributes=kept | extra, start_time=_start_ns(name, kept)
            )
            manager.__enter__()
        except Exception:
            yield
            return
        try:
            yield
        finally:
            with suppress(Exception):
                manager.__exit__(None, None, None)


__all__ = [
    "ADAPTER",
    "ALLOWED",
    "LANGFUSE_CLOUD",
    "MODULE",
    "OtelTracer",
    "filtered",
    "langfuse_endpoint",
    "langfuse_fields",
]
