"""P5-07: the ledger needs approvals, and a needed module that is off is named (I3, I6, S41)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from dispute_agent.api.full_run import run_full
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.enums import CaseState, ReviewCode
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.registry.validation import validate_modules
from tests.unit.registry.test_validation import a_file

NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def full(name: str, **kwargs: Any) -> Any:
    return run_full(
        name, policy=PolicyConfig(), clock=FakeClock(NOW), ids=FakeIdGenerator(), **kwargs
    )


class TestStartupRefuses:
    def test_the_ledger_without_approvals_never_starts_and_names_both(self) -> None:
        with pytest.raises(ConfigError) as refused:
            validate_modules(a_file(ledger={"enabled": True}, approvals={"enabled": False}))
        assert "ledger" in str(refused.value)
        assert "approvals" in str(refused.value)


class TestS41ANeededModuleThatIsOffIsNamed:
    @pytest.mark.parametrize(
        ("fixture", "off", "module"),
        [
            ("calc_error_wrong_slab", ("ledger",), "ledger"),
            ("calc_error_wrong_slab", ("approvals",), "approvals"),
            ("scanner_fault", ("email",), "email"),
        ],
    )
    def test_the_case_goes_to_a_person_naming_the_module(
        self, fixture: str, off: tuple[str, ...], module: str
    ) -> None:
        report = full(fixture, modules_off=off)
        assert report.case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert report.case.review_reason.code is ReviewCode.MODULE_UNAVAILABLE
        assert report.case.review_reason.module == module
        assert report.ledger_credits == 0
        assert report.emails_sent == 0
