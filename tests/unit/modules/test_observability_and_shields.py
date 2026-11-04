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
