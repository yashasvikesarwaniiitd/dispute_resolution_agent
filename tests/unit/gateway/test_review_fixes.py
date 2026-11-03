"""Regression tests for the Phase 4P safety review (findings M1-M4 and the LOWs).

Each test is one finding, written so it fails against the code the review read. The fence
attacks (H1) live with the other attack shapes in tests/contract/test_prompt_registry.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import openai
import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.policy import load_policy, with_llm_overrides
from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
from dispute_agent.kernel.domain.errors import (
    ConfigError,
    GuardrailBlocked,
    InvalidModelOutput,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway
from dispute_agent.kernel.policy.config import LlmConfig, LlmPrimaryConfig
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from dispute_agent.modules.model_gateway.foundry_agent_service.client import map_error
from tests.foundry_stub import PRICE, deployed_for
from tests.model_fixtures import request, valid_answer

ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def guard(primary: Any, tmp_path: Path) -> tuple[BudgetedModelGateway, Any]:
    ledger = open_model_call_log(tmp_path / "calls.sqlite3")
    return (
        BudgetedModelGateway(
            primary=primary,
            route="foundry_agent_service",
            ledger=ledger,
            limits=LlmConfig(),
            money=LlmPrimaryConfig(),
            clock=FakeClock(NOW),
            ids=FakeIdGenerator(),
        ),
        ledger,
    )


class TestM1PaidCallsAreNeverRecordedAsFree:
    def test_a_timeout_on_every_attempt_is_charged_at_worst_case(self, tmp_path: Path) -> None:
        """The scenario that made the $150 cap read $0 forever."""
        foundry, _ = deployed_for(request().prompt, ["timeout"])
        gateway, ledger = guard(foundry, tmp_path)
        with pytest.raises(TransientPortError):
            gateway.complete(request(max_output_tokens=1200))
        one_call_at_most = PRICE.cost(0, 1200)
        assert ledger.spend_usd("foundry_agent_service") >= 3 * one_call_at_most

    def test_a_timeout_then_a_success_records_both(self, tmp_path: Path) -> None:
        foundry, _ = deployed_for(request().prompt, ["timeout", valid_answer()])
        gateway, _ = guard(foundry, tmp_path)
        usage = gateway.complete(request(max_output_tokens=1200)).usage
        assert usage.cost_usd >= PRICE.cost(0, 1200)

    def test_a_rate_limit_is_not_charged(self, tmp_path: Path) -> None:
        """A 429 never reached a model; charging it would make the cap trip on nothing."""
        foundry, _ = deployed_for(request().prompt, ["rate_limit", valid_answer()])
        gateway, _ = guard(foundry, tmp_path)
        usage = gateway.complete(request(max_output_tokens=1200)).usage
        assert usage.cost_usd < PRICE.cost(0, 1200)

    def test_a_content_filter_stop_mid_answer_is_charged(self, tmp_path: Path) -> None:
        foundry, _ = deployed_for(request().prompt, [("incomplete", "content_filter", "")])
        gateway, ledger = guard(foundry, tmp_path)
        with pytest.raises(GuardrailBlocked):
            gateway.complete(request())
        assert ledger.spend_usd("foundry_agent_service") > 0

    def test_an_invalid_answer_then_a_block_keeps_the_first_answer_s_cost(
        self, tmp_path: Path
    ) -> None:
        gateway, ledger = guard(FakeModelGateway(script=["not json", "guardrail"]), tmp_path)
        with pytest.raises(GuardrailBlocked):
            gateway.complete(request())
        assert ledger.spend_usd("foundry_agent_service") > 0

    def test_the_worst_case_counts_the_agent_s_instructions(self) -> None:
        """They are billed as input even though we do not send them."""
        long_system = request().prompt.model_copy(
            update={
                "messages": (
                    request().prompt.messages[0].model_copy(update={"content": "x" * 9000}),
                    request().prompt.messages[1],
                )
            }
        )
        req = request().model_copy(update={"prompt": long_system})
        foundry, stub = deployed_for(long_system, [valid_answer()])
        stub.report_usage = False
        usage = foundry.complete(req).usage
        assert usage.input_tokens >= 9000 // 3


class TestM2AnUnknownProviderErrorIsTypedAndRecorded:
    def test_an_unmapped_sdk_error_becomes_a_typed_one(self) -> None:
        mapped = map_error(openai.OpenAIError("something new"))
        assert isinstance(mapped, PermanentPortError)
        assert mapped.maybe_billed is True

    def test_a_non_kernel_exception_is_still_recorded(self, tmp_path: Path) -> None:
        def explode(_: object) -> str:
            raise RuntimeError("a bug in an adapter")

        gateway, ledger = guard(FakeModelGateway(responder=explode), tmp_path)
        bare = request().model_copy(update={"case_id": "case_01HQ"})
        with pytest.raises(RuntimeError):
            gateway.complete(bare)
        assert ledger.calls_for_case("case_01HQ") == 1


class TestM3ABadAnswerIsNeverHandedToTheRules:
    def test_invalid_then_unreachable_is_invalid_at_the_gateway(self) -> None:
        with pytest.raises(InvalidModelOutput):
            FakeModelGateway(script=["not json", "rate_limit"]).complete(request())

    def test_invalid_then_unavailable_is_invalid_at_the_gateway(self) -> None:
        with pytest.raises(InvalidModelOutput):
            FakeModelGateway(script=["not json", "unavailable"]).complete(request())

    def test_a_first_failure_with_nothing_rejected_stays_transient(self) -> None:
        """The control: an unreachable model on the first try is a fallback case."""
        with pytest.raises(TransientPortError):
            FakeModelGateway(script=["rate_limit"]).complete(request())


class TestLows:
    def test_a_spend_cap_of_zero_is_an_error_not_the_default(self) -> None:
        with pytest.raises(ConfigError):
            with_llm_overrides(
                load_policy(ROOT / "config"),
                credit_expires_on=None,
                allow_paid_usage=False,
                spend_cap_usd=Decimal(0),
            )

    def test_the_call_log_path_does_not_depend_on_the_working_directory(self) -> None:
        from dispute_agent.app import FOUNDRY_LOCK, MODEL_CALL_LOG, PRICING, PROMPTS_DIR

        for path in (MODEL_CALL_LOG, FOUNDRY_LOCK, PRICING, PROMPTS_DIR):
            assert path.is_absolute(), path

    def test_an_inactive_prompt_is_refused_at_start_up(self) -> None:
        from dispute_agent.app import build_llm_reasoner
        from dispute_agent.infra.settings import Settings
        from dispute_agent.kernel.policy.config import PolicyConfig

        with pytest.raises(ConfigError, match="PROMPTS_ALLOW_INACTIVE"):
            build_llm_reasoner(
                FakeModelGateway(),
                settings=Settings(_env_file=None),
                policy=PolicyConfig(),
                clock=FakeClock(NOW),
                ids=FakeIdGenerator(),
            )
