"""Phase 5, end to end: the credit flow and its verification (P5-04, P5-05, I2, I3, I4, I13).

Against the real SQLite case store, the real simulated ledger in its own file, and the Phase 3
email and Jira fakes. Paths and counts are the scenario catalogue's (eval-scenarios.yaml).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from dispute_agent.api.full_run import run_full
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.enums import (
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
    ReviewCode,
)
from dispute_agent.kernel.policy.config import PolicyConfig

S = CaseState
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def full(name: str, **kwargs: Any) -> Any:
    return run_full(
        name, policy=PolicyConfig(), clock=FakeClock(NOW), ids=FakeIdGenerator(), **kwargs
    )


class TestS01ACreditIsPostedVerifiedAndTold:
    def test_the_path_is_the_catalogue_s(self) -> None:
        report = full("calc_error_wrong_slab")
        assert report.path == [
            S.RECEIVED, S.GATHERING_EVIDENCE, S.CALCULATING, S.INVESTIGATING,
            S.AWAITING_APPROVAL, S.EXECUTING, S.VERIFYING,
            S.AWAITING_APPROVAL, S.EXECUTING, S.VERIFYING, S.RESOLVED_CREDITED,
        ]  # fmt: skip

    def test_the_counts_are_the_catalogue_s(self) -> None:
        report = full("calc_error_wrong_slab")
        assert (report.ledger_credits, report.emails_sent, len(report.approvals)) == (1, 1, 2)

    def test_the_credit_approval_names_the_amount_code_derived(self) -> None:
        report = full("calc_error_wrong_slab")
        credit = next(a for a in report.approvals if a.request.subject is ApprovalSubject.CREDIT)
        assert credit.request.amount is not None
        assert credit.request.amount.to_paise() > 0

    def test_the_confirmation_cites_the_credited_amount(self) -> None:
        report = full("calc_error_wrong_slab")
        assert any("credit posted" in s for s in report.subjects)

    def test_the_proposal_that_started_it_is_on_the_record(self) -> None:
        report = full("calc_error_wrong_slab")
        assert [p.action.value for p in report.proposals] == ["propose_credit"]


class TestCreditApprovalIsTheGate:
    def test_s10_rejecting_a_credit_hands_the_case_to_a_person_and_posts_nothing(self) -> None:
        """D40: the reviewer disagrees with a code-proven amount; no re-planning."""
        report = full("calc_error_wrong_slab", decide=lambda a: ApprovalDecision.REJECTED)
        assert report.case.state is S.NEEDS_HUMAN_REVIEW
        assert report.case.review_reason.code is ReviewCode.CREDIT_REJECTED
        assert report.ledger_credits == 0

    def test_no_decision_means_nothing_moves(self) -> None:
        report = full("calc_error_wrong_slab", decide=lambda a: None)
        assert report.case.state is S.AWAITING_APPROVAL
        assert report.ledger_credits == 0


class TestVerification:
    def test_s13a_a_credit_posted_but_unacknowledged_is_found_and_resolves(self) -> None:
        report = full("calc_error_wrong_slab", ledger_failure="timeout_after")
        assert report.case.state is S.RESOLVED_CREDITED
        assert report.ledger_credits == 1

    def test_s13b_a_wrong_amount_is_never_resolved(self) -> None:
        """I4: what is out there is not what was approved -> UNKNOWN, a person decides."""
        report = full("calc_error_wrong_slab", ledger_failure="wrong_amount")
        assert report.case.state is S.NEEDS_HUMAN_REVIEW
        assert report.case.review_reason.code is ReviewCode.VERIFICATION_FAILED
        assert report.emails_sent == 0  # never told a credit that is wrong

    def test_a_late_visible_credit_verifies_after_waiting(self) -> None:
        report = full("calc_error_wrong_slab", ledger_failure="delayed_visibility")
        assert report.case.state is S.RESOLVED_CREDITED

    def test_a_ledger_refusal_is_a_failed_action_for_a_person(self) -> None:
        report = full("calc_error_wrong_slab", ledger_failure="reject_400")
        assert report.case.state is S.NEEDS_HUMAN_REVIEW
        assert report.case.review_reason.code is ReviewCode.ACTION_FAILED
        assert report.ledger_credits == 0

    def test_a_timeout_before_writing_is_retried_safely(self) -> None:
        """The ledger is idempotent: the retry after a timeout posts once."""
        report = full("calc_error_wrong_slab", ledger_failure="timeout_before")
        assert report.ledger_credits <= 1


class TestMessagesGoOutOnlyAfterApproval:
    def test_an_evidence_request_is_sent_once_after_approval_and_waits_for_the_seller(
        self,
    ) -> None:
        report = full("scanner_fault")
        assert report.case.state is S.AWAITING_SELLER
        assert report.emails_sent == 1
        assert report.path[-4:] == [
            S.AWAITING_APPROVAL,
            S.EXECUTING,
            S.VERIFYING,
            S.AWAITING_SELLER,
        ]

    def test_without_approval_nothing_is_sent(self) -> None:
        report = full("scanner_fault", decide=lambda a: None)
        assert report.emails_sent == 0


class TestTheRemeasurementIssueNeedsNoApproval:
    def test_an_allowed_issue_is_planned_sent_and_verified(self) -> None:
        """T06: ALLOW plans through the executor; the issue is read back before AWAITING_OPS."""
        from dispute_agent.modules.model_gateway.fake.gateway import FakeModelGateway
        from dispute_agent.modules.reasoning.llm.reasoner import LlmReasoner
        from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry
        from tests.model_fixtures import PROMPTS, valid_answer

        clock, ids = FakeClock(NOW), FakeIdGenerator()
        remeasure = valid_answer(
            action="request_remeasurement",
            action_parameters={"remeasurement_question": "Please remeasure the parcel."},
        )
        reasoner = LlmReasoner(
            prompts=FilePromptRegistry(PROMPTS, allow_inactive=True),
            gateway=FakeModelGateway(script=[remeasure]),
            ids=ids,
            clock=clock,
        )
        report = run_full(
            "scanner_fault", policy=PolicyConfig(), clock=clock, ids=ids, reasoner=reasoner
        )
        assert report.jira_issues == 1
        assert report.case.state is S.AWAITING_OPS
        assert report.approvals == []


@pytest.mark.parametrize("name", ["kurta_box_larger", "not_delivered", "multi_error"])
def test_cases_the_rules_cannot_credit_never_reach_the_ledger(name: str) -> None:
    assert full(name).ledger_credits == 0


class TestTheReviewerSeesWhy:
    """P5-03: a credit in the queue carries its amount, its reason and its evidence."""

    def test_a_pending_credit_shows_the_correction_and_the_evidence(self) -> None:
        from dispute_agent.infra.sqlite.engine import create_sqlite_engine
        from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
        from dispute_agent.modules.approvals.streamlit_ui.console import ReviewConsole, sign_in

        report = full("calc_error_wrong_slab", decide=lambda a: None)
        assert report.database is not None
        engine = create_sqlite_engine(report.database)
        try:
            with SqliteUnitOfWork(engine, clock=FakeClock(NOW)) as work:
                console = ReviewConsole(
                    store=work.approvals,
                    clock=FakeClock(NOW),
                    reviewer=sign_in("t" * 20, "t" * 20, "owner"),
                )
                [pending] = console.queue()
                item = console.view(pending, intents=work.intents, assessments=work.assessments)
        finally:
            engine.dispose()
        assert item.is_credit
        assert item.credit_reason == "calculation_error"
        assert item.credit_amount is not None and item.credit_amount.startswith("\u20b9")
        labels = [label for label, _ in item.evidence]
        assert labels, "the reviewer must see the facts behind the amount"
        assert item.matches_approval


def test_the_demo_sequence_runs_end_to_end_offline(capsys: pytest.CaptureFixture[str]) -> None:
    """P5-09's test_demo_sequence: the same sequence against fakes, so the demo cannot rot."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "demo.py"
    spec = importlib.util.spec_from_file_location("demo", path)
    assert spec is not None and spec.loader is not None
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    assert demo.offline("calc_error_wrong_slab", agent=False) == 0
    out = capsys.readouterr().out
    assert "RESOLVED_CREDITED" in out
    assert "ledger credits: 1" in out
