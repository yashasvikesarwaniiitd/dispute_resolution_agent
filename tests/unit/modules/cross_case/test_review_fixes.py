"""Phase 7 safety review: one test per finding, each failing on the code it reviewed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.policy import load_policy
from dispute_agent.infra.sqlite.outcome_outbox import SqliteOutcomeOutbox
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.errors import ExternalNotFound, TransientPortError
from dispute_agent.kernel.domain.ids import CaseId, OutcomeId
from dispute_agent.kernel.domain.patterns import CaseOutcome
from dispute_agent.modules.cross_case.analyzer.observer import SqlOutcomeObserver
from dispute_agent.modules.cross_case.sql_context import SqlPatternContextProvider
from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository
from dispute_agent.modules.ticketing.write_port import IssueWritePort
from tests.support.patterns import T0, add_case, add_pattern, facts, new_engine
from tests.unit.modules.cross_case.test_investigations import setup

DAY = timedelta(days=1)


class TestMed1TheAnchoringControlIsAlwaysOn:
    def test_the_scenario_and_live_reasoner_is_two_pass(self) -> None:
        from pathlib import Path

        from dispute_agent.app import build_scenario_models
        from dispute_agent.infra.ids import FakeIdGenerator
        from dispute_agent.modules.reasoning.llm.two_pass import TwoPassReasoner

        policy = load_policy(Path(__file__).resolve().parents[4] / "config")
        models = build_scenario_models(lambda req: {}, policy, FakeClock(T0), FakeIdGenerator())
        assert isinstance(models.reasoner, TwoPassReasoner)


class TestMed2AnUnknownInvestigationGoesToAPerson:
    def test_an_ambiguous_write_is_unknown_never_retried_and_listed(self) -> None:
        s = setup()
        s.tickets.faults["upsert_issue"] = "marker_fails"  # created, but unfindable by key
        first = s.investigations.open(s.pattern_id, s.clock.now())
        assert first.status == "UNKNOWN" and first.reason.startswith("needs_person")
        s.tickets.faults.clear()
        again = s.investigations.open(s.pattern_id, s.clock.now() + DAY)
        assert again.status == "UNKNOWN"
        assert s.tickets.create_attempts() == 1  # never written a second time
        assert s.investigations.needs_person() == [s.pattern_id]
        assert s.investigations.issue_key(s.pattern_id) is None  # no findings loop on it

    def test_an_issue_not_readable_by_the_deadline_becomes_unknown(self) -> None:
        s = setup()

        @dataclass
        class Invisible(IssueWritePort):
            def read_back(self, receipt: Any) -> Any:
                raise ExternalNotFound("not indexed yet")

        s.investigations.port = lambda subject: Invisible(s.tickets, subject, "WD")
        assert s.investigations.open(s.pattern_id, s.clock.now()).status == "SENT"
        late = s.investigations.open(s.pattern_id, s.clock.now() + 2 * DAY)
        assert (late.status, late.reason) == ("UNKNOWN", "needs_person:verify_deadline_passed")


class TestLow4FailedWritesAreCapped:
    def test_three_failures_then_a_person(self) -> None:
        s = setup()
        s.tickets.faults["upsert_issue"] = "permanent"
        for n in range(5):
            result = s.investigations.open(s.pattern_id, s.clock.now() + n * DAY)
        assert result.reason == "retries_exhausted:needs_person"
        assert s.tickets.create_attempts() == 3


class TestLow5NoModelTextInJira:
    def test_a_model_written_summary_is_not_copied_into_the_issue(self) -> None:
        s = setup()
        s.investigations.repository.set_summary(
            s.pattern_id,
            "Hub staff look careless; sellers deserve refunds.",
            "model",
            s.clock.now(),
        )
        s.investigations.open(s.pattern_id, s.clock.now())
        [issue] = s.tickets._issues
        assert "careless" not in issue.spec.description


class TestMed3TheOutboxCannotBeBlocked:
    def test_an_unplaceable_shipment_is_recorded_and_assumed_informed(self) -> None:
        engine = new_engine()
        add_case(engine, "case_1")
        repo = PatternRepository(engine)
        add_pattern(repo, "pat_1", "hub", "HUB-01", [], seen=T0 - DAY)

        def unknown(ref: str) -> dict[str, str]:
            raise LookupError(ref)

        observer = SqlOutcomeObserver(repo, unknown, FakeClock(T0))
        observer.on_outcome(
            CaseOutcome(
                id=OutcomeId("out_1"),
                case_id=CaseId("case_1"),
                state=CaseState.WITHDRAWN,
                resolved=False,
                facts=facts(claim="CHG-case_1", shipment="SHP-nowhere"),
                closed_at=T0,
            )
        )
        assert len(repo.outcomes()) == 1
        assert repo.linked("pat_1", "informed") == ["case_1"]  # assume it could have seen it

    def test_a_row_that_keeps_failing_goes_to_the_back(self) -> None:
        engine = new_engine()
        for case_id in ("case_old", "case_new"):
            add_case(engine, case_id)
        with engine.begin() as conn:
            box = SqliteOutcomeOutbox(conn)
            for n, case_id in enumerate(("case_old", "case_new")):
                box.add(
                    CaseOutcome(
                        id=OutcomeId(f"out_{n}"),
                        case_id=CaseId(case_id),
                        state=CaseState.WITHDRAWN,
                        resolved=False,
                        facts=facts(),
                        closed_at=T0,
                    ),
                    T0 + n * DAY,
                )
            box.mark_failed("out_0", "TransientPortError")
        with engine.connect() as conn:
            first = SqliteOutcomeOutbox(conn).undelivered(limit=1)
        assert [str(o.id) for o in first] == ["out_1"]


class TestUnexpectedProviderErrorsAreCaution:
    def test_any_error_becomes_transient(self) -> None:
        engine = new_engine()

        def broken(ref: str) -> dict[str, str]:
            raise TypeError("a bug in the key lookup")

        provider = SqlPatternContextProvider(PatternRepository(engine), broken)
        with pytest.raises(TransientPortError):
            provider.patterns_for(facts())


class TestLow1TheJobKeepsGoing:
    def test_findings_that_cannot_be_read_do_not_stop_the_rest(self) -> None:
        from dispute_agent.modules.cross_case.analyzer import PatternAnalyzer, Thresholds
        from dispute_agent.modules.cross_case.analyzer.confirmation import (
            ConfirmationRule,
            FindingsReader,
        )
        from dispute_agent.modules.cross_case.analyzer.job import CrossCaseJob
        from dispute_agent.modules.cross_case.analyzer.recurrence import RecurrenceMonitor
        from dispute_agent.modules.evidence.synthetic_shipments.library import (
            SyntheticLibrarySource,
        )

        s = setup()
        repo = s.investigations.repository
        keys = SyntheticLibrarySource().keys_for

        def no_findings(since: Any) -> Any:
            raise NotImplementedError("this ticketing adapter cannot list findings")

        rule = ConfirmationRule(repo)
        job = CrossCaseJob(
            analyzer=PatternAnalyzer(repo, keys, s.investigations.ids),
            investigations=s.investigations,
            confirmation=rule,
            findings=FindingsReader(rule, no_findings, s.investigations.issue_key),
            recurrence=RecurrenceMonitor(repo, keys, Thresholds()),
        )
        report = job.run(s.clock.now())
        assert report.errors == ["findings:NotImplementedError"]
        assert [r.status for r in report.investigations] == ["VERIFIED"]
        assert not job.due(s.clock.now() + timedelta(minutes=5))  # counted as run: hourly
