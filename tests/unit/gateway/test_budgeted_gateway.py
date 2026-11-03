"""The spending guard (ADR 0017, D18, I14), against the real SQLite call log.

Each test tries to get a model call past a limit, or to make a call that was paid for
disappear from the count. The log is the real one, in its own file, so "recorded" here
means written and committed, not appended to a list.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.infra.sqlite.model_call_log import open_model_call_log
from dispute_agent.kernel.domain.errors import (
    BudgetExceeded,
    GuardrailBlocked,
    InvalidModelOutput,
    ModuleUnavailable,
)
from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.gateway.budgeted import BudgetedModelGateway
from dispute_agent.kernel.policy.config import LlmConfig, LlmPrimaryConfig
from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
from tests.model_fixtures import SECRET_MARKER, request, valid_answer

if TYPE_CHECKING:
    from pathlib import Path

    from dispute_agent.infra.sqlite.model_call_log import SqliteModelUsageLedger

NOW = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)
CASE = CaseId("case_01HQ")


def guarded(
    tmp_path: Path,
    script: list[object],
    *,
    max_calls: int = 6,
    cap: str = "150.00",
    expires: date | None = None,
    paid: bool = False,
    now: datetime = NOW,
) -> tuple[BudgetedModelGateway, FakeModelGateway, SqliteModelUsageLedger]:
    ledger = open_model_call_log(tmp_path / "model_calls.sqlite3")
    inner = FakeModelGateway(script=list(script))  # type: ignore[arg-type]
    gateway = BudgetedModelGateway(
        primary=inner,
        route="fake",
        ledger=ledger,
        limits=LlmConfig(max_calls_per_case=max_calls),
        money=LlmPrimaryConfig(
            spend_cap_usd=Decimal(cap), credit_expires_on=expires, allow_paid_usage=paid
        ),
        clock=FakeClock(now),
        ids=FakeIdGenerator(),
    )
    return gateway, inner, ledger


def for_case() -> object:
    return request().model_copy(update={"case_id": CASE})


class TestPerCaseCap:
    def test_the_call_after_the_cap_never_reaches_the_model(self, tmp_path: Path) -> None:
        gateway, inner, ledger = guarded(tmp_path, [valid_answer()] * 5, max_calls=2)
        gateway.complete(for_case())  # type: ignore[arg-type]
        gateway.complete(for_case())  # type: ignore[arg-type]
        with pytest.raises(BudgetExceeded):
            gateway.complete(for_case())  # type: ignore[arg-type]
        assert inner.provider_calls() == 2
        assert ledger.calls_for_case(CASE) == 2

    def test_failed_calls_count_too(self, tmp_path: Path) -> None:
        """A model that keeps answering badly must not get unlimited tries."""
        gateway, inner, _ = guarded(tmp_path, ["bad"] * 10, max_calls=2)
        for _ in range(2):
            with pytest.raises(InvalidModelOutput):
                gateway.complete(for_case())  # type: ignore[arg-type]
        with pytest.raises(BudgetExceeded):
            gateway.complete(for_case())  # type: ignore[arg-type]
        assert inner.provider_calls() == 4  # two calls, each with its one retry


class TestSpendCap:
    def test_spend_at_the_cap_refuses_the_next_call(self, tmp_path: Path) -> None:
        gateway, inner, ledger = guarded(tmp_path, [valid_answer()] * 3, cap="0.000001")
        gateway.complete(request())
        assert ledger.spend_usd("fake") >= Decimal("0.000001")
        with pytest.raises(BudgetExceeded):
            gateway.complete(request())
        assert inner.provider_calls() == 1

    def test_the_cost_of_invalid_answers_is_counted(self, tmp_path: Path) -> None:
        """Both bad attempts were paid for; the cap must see them."""
        gateway, _, ledger = guarded(tmp_path, ["bad", "bad"])
        with pytest.raises(InvalidModelOutput):
            gateway.complete(request())
        assert ledger.spend_usd("fake") > 0

    def test_cost_is_rounded_up_never_down(self, tmp_path: Path) -> None:
        gateway, _, ledger = guarded(tmp_path, [valid_answer()])
        response = gateway.complete(request())
        assert ledger.spend_usd("fake") >= response.usage.cost_usd


class TestCreditWindow:
    def test_after_the_credit_expires_calls_stop(self, tmp_path: Path) -> None:
        gateway, inner, _ = guarded(tmp_path, [valid_answer()], expires=date(2026, 11, 3))
        with pytest.raises(BudgetExceeded):
            gateway.complete(request())
        assert inner.provider_calls() == 0

    def test_the_last_day_of_the_credit_still_works(self, tmp_path: Path) -> None:
        gateway, _, _ = guarded(tmp_path, [valid_answer()], expires=date(2026, 11, 4))
        gateway.complete(request())

    def test_only_the_owner_s_explicit_switch_allows_paid_usage(self, tmp_path: Path) -> None:
        gateway, _, _ = guarded(tmp_path, [valid_answer()], expires=date(2026, 11, 3), paid=True)
        gateway.complete(request())

    def test_paid_usage_is_off_by_default(self) -> None:
        assert LlmPrimaryConfig().allow_paid_usage is False


class TestTheLog:
    def test_every_outcome_is_recorded(self, tmp_path: Path) -> None:
        gateway, _, ledger = guarded(
            tmp_path, [valid_answer(), "bad", "bad", "guardrail", "unavailable"], max_calls=99
        )
        gateway.complete(for_case())  # type: ignore[arg-type]
        for raised in (InvalidModelOutput, GuardrailBlocked, ModuleUnavailable):
            with pytest.raises(raised):
                gateway.complete(for_case())  # type: ignore[arg-type]
        assert ledger.calls_for_case(CASE) == 4

    def test_no_prompt_or_answer_text_is_stored(self, tmp_path: Path) -> None:
        """I14. The row type has no field for it; this checks the file itself."""
        gateway, _, _ = guarded(tmp_path, [valid_answer(rationale=SECRET_MARKER)])
        gateway.complete(request())
        raw = (tmp_path / "model_calls.sqlite3").read_bytes()
        assert SECRET_MARKER.encode() not in raw

    def test_the_record_survives_without_any_case_transaction(self, tmp_path: Path) -> None:
        """ADR 0030: committed at once, in its own file."""
        gateway, _, _ = guarded(tmp_path, [valid_answer()])
        gateway.complete(for_case())  # type: ignore[arg-type]
        reopened = open_model_call_log(tmp_path / "model_calls.sqlite3")
        assert reopened.calls_for_case(CASE) == 1
