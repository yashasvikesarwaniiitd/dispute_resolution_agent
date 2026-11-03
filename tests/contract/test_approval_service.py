"""Contract: ApprovalService (kernel-interfaces.md 6.4, approvals.md, ADR 0013).

The port notifies; it never decides. The decision arrives as an APPROVAL_DECIDED inbound
event and the kernel's approvals table is the record (I3). So the suite's job is largely to
check that this adapter cannot become a second source of truth: `request` returns None, and
there is no method that hands back a decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import ApprovalService

ADAPTERS = [
    pending("store_backed", phase="Phase 3 (card P3-06)"),
]


@pytest.fixture(params=params(ADAPTERS))
def approvals(request: pytest.FixtureRequest) -> ApprovalService:
    return cast("ApprovalService", build_or_skip(request.param))


class TestRequesting:
    def test_requesting_twice_for_one_approval_notifies_once(
        self, approvals: ApprovalService
    ) -> None:
        """Idempotent on approval.id: the runner retries while notified_at is null."""

    def test_a_transient_failure_raises_rather_than_silently_succeeding(
        self, approvals: ApprovalService
    ) -> None:
        """The case waits and the runner retries; the approval row is already the record."""

    def test_it_returns_nothing(self, approvals: ApprovalService) -> None:
        """ADR 0013: a port that could return a decision would be a second source of truth,
        and the easiest one to forge."""


class TestWithdrawing:
    def test_withdrawing_twice_is_harmless(self, approvals: ApprovalService) -> None: ...

    def test_withdrawing_an_unknown_approval_is_harmless(
        self, approvals: ApprovalService
    ) -> None: ...
