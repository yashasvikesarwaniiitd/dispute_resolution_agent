"""Tracing content policy, Prompt Shields annotations, spend report and llm-check (P4b-08/09/10, P4-12)."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from dispute_agent.kernel.domain.reasoning import ModelResponse, ModelUsage
from dispute_agent.modules.model_gateway.foundry_agent_service.client import shield_annotations
from dispute_agent.modules.tracing.otel import OtelTracer, filtered
from tests.anthropic_stub import haiku
from tests.model_fixtures import SECRET_MARKER, valid_answer

ROOT = Path(__file__).resolve().parents[3]


def script(name: str) -> Any:
    """Load a script from scripts/ by path; the folder is not a package."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SENTINEL_SECRET = "sk-" + "ant-sentinel-secret-0123456789abcdef"


def otel() -> tuple[OtelTracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OtelTracer(provider.get_tracer("test")), exporter


class TestSpansCarryNoContent:
    def test_attributes_off_the_allow_list_are_dropped(self) -> None:
        tracer, exporter = otel()
        with tracer.span(
            "model.call",
            route="foundry_agent_service",
            prompt_text=SECRET_MARKER,
            api_key=SENTINEL_SECRET,
            input_tokens="12",
        ):
            pass
        attributes = dict(exporter.get_finished_spans()[0].attributes or {})
        assert attributes == {"route": "foundry_agent_service", "input_tokens": "12"}

    def test_a_traced_run_never_exports_content_or_secrets(self) -> None:
        """P4b-10's named test: sentinel content and a sentinel secret appear in no span."""
        tracer, exporter = otel()
        for name in ("step.investigate", "model.call", "policy.evaluate", "dispatch"):
            with tracer.span(name, case_id="case_1", body=SECRET_MARKER, token=SENTINEL_SECRET):
                pass
        dumped = json.dumps(
            [dict(s.attributes or {}) | {"name": s.name} for s in exporter.get_finished_spans()]
        )
        assert SECRET_MARKER not in dumped
        assert SENTINEL_SECRET not in dumped

    def test_an_unknown_span_kind_keeps_no_attributes(self) -> None:
        assert filtered("something.new", {"case_id": "x", "body": "y"}) == {}

    def test_a_broken_tracer_never_fails_the_caller(self) -> None:
        class Broken:
            def start_as_current_span(self, *a: Any, **k: Any) -> Any:
                raise RuntimeError("exporter down")

        with OtelTracer(Broken()).span("step.x", case_id="c"):
            ran = True
        assert ran


def langfuse() -> tuple[OtelTracer, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return OtelTracer(provider.get_tracer("test"), langfuse=True), exporter


class TestLangfuse:
    """ADR 0057: an AI call reaches Langfuse as a generation, and still carries no content."""

    CALL = {  # noqa: RUF012 - read only
        "purpose": "seller_reply_interpreter",
        "route": "anthropic_direct",
        "model_id": "claude-haiku-5-5",
        "input_tokens": "1349",
        "output_tokens": "120",
        "latency_ms": "2500",
        "cost_usd": "0.0021",
        "outcome": "ok",
        "case_id": "case_1",
    }

    def test_a_model_call_is_a_generation_with_tokens_cost_and_session(self) -> None:
        tracer, exporter = langfuse()
        with tracer.span("model.call", **self.CALL):
            pass
        [span] = exporter.get_finished_spans()
        attributes = dict(span.attributes or {})
        assert attributes["langfuse.observation.type"] == "generation"
        assert attributes["langfuse.observation.model.name"] == "claude-haiku-5-5"
        assert json.loads(str(attributes["langfuse.observation.usage_details"])) == {
            "input": 1349,
            "output": 120,
        }
        assert json.loads(str(attributes["langfuse.observation.cost_details"])) == {"total": 0.0021}
        assert attributes["langfuse.session.id"] == "case_1"

    def test_the_span_lasts_as_long_as_the_call(self) -> None:
        """The span opens after the call returns; without a back-dated start, latency is 0."""
        tracer, exporter = langfuse()
        with tracer.span("model.call", **self.CALL):
            pass
        [span] = exporter.get_finished_spans()
        assert span.start_time is not None and span.end_time is not None
        assert abs((span.end_time - span.start_time) / 1e6 - 2500) < 200

    def test_a_failed_call_names_no_model_and_still_carries_no_content(self) -> None:
        tracer, exporter = langfuse()
        with tracer.span(
            "model.call",
            model_id="unknown",
            outcome="error",
            body=SECRET_MARKER,
            token=SENTINEL_SECRET,
        ):
            pass
        attributes = dict(exporter.get_finished_spans()[0].attributes or {})
        assert "langfuse.observation.model.name" not in attributes
        assert SECRET_MARKER not in json.dumps(attributes)
        assert SENTINEL_SECRET not in json.dumps(attributes)

    def test_the_plain_tracer_adds_no_langfuse_names(self) -> None:
        tracer, exporter = otel()
        with tracer.span("model.call", **self.CALL):
            pass
        attributes = dict(exporter.get_finished_spans()[0].attributes or {})
        assert not [k for k in attributes if k.startswith("langfuse.")]

    def test_the_endpoint_and_basic_auth(self) -> None:
        import base64

        from dispute_agent.modules.tracing.otel import langfuse_endpoint

        url, headers = langfuse_endpoint("https://cloud.langfuse.com/", "pk-lf-1", "sk-lf-2")
        assert url == "https://cloud.langfuse.com/api/public/otel/v1/traces"
        scheme, token = headers["Authorization"].split(" ")
        assert scheme == "Basic"
        assert base64.b64decode(token).decode() == "pk-lf-1:sk-lf-2"
        assert headers["x-langfuse-ingestion-version"] == "4"

    def test_without_keys_the_app_wires_the_noop_tracer(self) -> None:
        from dispute_agent.app import _build_langfuse
        from dispute_agent.infra.settings import Settings
        from dispute_agent.modules.tracing.noop import NoopTracer

        assert isinstance(_build_langfuse(Settings(_env_file=None)), NoopTracer)
        assert isinstance(_build_langfuse(None), NoopTracer)

    def test_with_keys_the_app_wires_langfuse(self) -> None:
        from pydantic import SecretStr

        from dispute_agent.app import _build_langfuse
        from dispute_agent.infra.settings import Settings

        settings = Settings(
            _env_file=None, langfuse_public_key="pk-lf-1", langfuse_secret_key=SecretStr("sk-lf-2")
        )
        tracer = _build_langfuse(settings)
        assert isinstance(tracer, OtelTracer)
        assert tracer._langfuse


class TestPromptShields:
    def test_detections_become_annotations(self) -> None:
        response = SimpleNamespace(
            model_extra={
                "content_filters": [
                    {
                        "content_filter_results": {
                            "indirect_attack": {"detected": True, "filtered": False},
                            "jailbreak": {"detected": False},
                        }
                    }
                ]
            }
        )
        assert shield_annotations(response) == ("prompt_shield:indirect_attack",)

    def test_an_unrecognised_shape_is_ignored_not_guessed(self) -> None:
        assert shield_annotations(SimpleNamespace(model_extra={"content_filters": "odd"})) == ()
        assert shield_annotations(SimpleNamespace()) == ()

    def test_a_detection_reaches_a_person_even_when_the_model_noticed_nothing(self) -> None:
        """I15: the annotation joins the proposal's flags, and R21 escalates on them."""
        from dispute_agent.api.agent_trial import run_trial
        from dispute_agent.infra.clock import FakeClock
        from dispute_agent.infra.ids import FakeIdGenerator
        from dispute_agent.kernel.domain.enums import ReviewCode
        from dispute_agent.kernel.policy.config import PolicyConfig
        from dispute_agent.modules.reasoning.llm.reasoner import LlmReasoner
        from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry
        from tests.e2e.test_phase3_investigate import NOW
        from tests.model_fixtures import PROMPTS

        class Shielded:
            def complete(self, req: Any) -> ModelResponse:
                return ModelResponse(
                    output=valid_answer(manipulation_flags=[]),
                    model_id="m",
                    route="foundry_agent_service",
                    usage=ModelUsage(
                        input_tokens=1, output_tokens=1, latency_ms=1, cost_usd=Decimal(0)
                    ),
                    screen_annotations=("prompt_shield:indirect_attack",),
                )

        clock, ids = FakeClock(NOW), FakeIdGenerator()
        reasoner = LlmReasoner(
            prompts=FilePromptRegistry(PROMPTS, allow_inactive=True),
            gateway=Shielded(),
            ids=ids,
            clock=clock,
        )
        report = run_trial("scanner_fault", reasoner, policy=PolicyConfig(), clock=clock, ids=ids)
        assert report.case is not None and report.case.review_reason is not None
        assert report.case.review_reason.code is ReviewCode.MANIPULATION_SUSPECTED
        assert report.proposal is not None
        assert "prompt_shield:indirect_attack" in report.proposal.manipulation_flags


class TestSpendReport:
    def test_warnings_at_fifty_and_eighty_percent(self) -> None:
        report = script("foundry_spend").report

        caps = {"foundry_agent_service": Decimal(100), "anthropic_direct": Decimal(10)}
        lines = report(
            {"foundry_agent_service": Decimal(55), "anthropic_direct": Decimal("8.5")}, caps
        )
        assert "over 50%" in lines[0]
        assert "over 80%" in lines[1]

    def test_under_half_has_no_warning(self) -> None:
        report = script("foundry_spend").report

        line = report(
            {"foundry_agent_service": Decimal(1)}, {"foundry_agent_service": Decimal(100)}
        )
        assert "over" not in line[0]


class TestLlmCheck:
    def test_the_output_is_ids_and_counts_only(self) -> None:
        """P4-12's named test: no prompt or response content in what it prints."""
        check = script("llm_check")
        PROBE_SYSTEM, PROBE_USER, probe = check.PROBE_SYSTEM, check.PROBE_USER, check.probe  # noqa: N806

        gateway, _ = haiku([{"ok": True}])
        line = probe(gateway)
        assert line.startswith("model=")
        for content in (PROBE_SYSTEM, PROBE_USER, '"ok"', "true"):
            assert content not in line
