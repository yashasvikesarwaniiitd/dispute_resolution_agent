"""The Phase 6 safety review: one test (or a few) per finding."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from dispute_agent.api.full_run import REVIEWER
from dispute_agent.app import STALLED_AFTER, create_app, tick_once
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import MessagePayload
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    CaseState,
    InboundEventType,
    MessageKind,
    ReviewCode,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import TransientPortError
from dispute_agent.kernel.domain.evidence import EvidenceRequirement
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.policy.rules.r04_contact_limit_case import ContactLimitCase
from dispute_agent.kernel.policy.rules.r05_contact_limit_daily import ContactLimitDaily
from dispute_agent.kernel.policy.signals import repeat_dispute_signal
from dispute_agent.modules.email.fake.channel import FakeMessageChannel
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from dispute_agent.packs.weight_dispute import eligibility as rules
from tests.unit.policy.test_rules import ctx, message_intent

ROOT = Path(__file__).resolve().parents[3]
HIGH_VALUE = fixture("calc_error_high_value")
FULL_LINE = (ROOT / "tests" / "unit" / "api" / "test_full_runtime.py").read_text(encoding="utf-8")
MODULES = FULL_LINE.split('FULL_LINE = """', 1)[1].split('"""', 1)[0]


def running_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, kill_switch: bool) -> Any:
    (tmp_path / "modules.yaml").write_text(MODULES, encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    engine.dispose()
    monkeypatch.setattr(
        "dispute_agent.app.Settings",
        lambda: Settings(app_env="test", test_seller_email=SELLER_CONTACT, kill_switch=kill_switch),
    )
    return create_app(config_dir=tmp_path, app_env="test", database=database)


def dispute() -> dict[str, object]:
    return {
        "source_event_id": "form-ks",
        "shipment_ref": HIGH_VALUE.shipment.ref,
        "claim_ref": HIGH_VALUE.shipment.claim_ref,
        "seller_ref": HIGH_VALUE.shipment.seller_id,
        "seller_contact": SELLER_CONTACT,
        "raised_at": "2026-11-04T09:00:00Z",
    }


class TestHigh1TheKillSwitchSettingStopsTheRunningApp:
    def test_kill_switch_true_posts_nothing_even_after_an_approval(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        app = running_app(tmp_path, monkeypatch, kill_switch=True)
        client = TestClient(app)
        body = client.post("/events", json=dispute()).json()
        runtime = app.state.application.runtime
        ledger = app.state.application.adapters.ledger
        if body["state"] == "AWAITING_APPROVAL":
            with runtime._uow() as work:
                [pending] = work.approvals.pending(None)
                work.approvals.decide(
                    pending.id, ApprovalDecision.APPROVED, REVIEWER, runtime._clock.now(), ""
                )
            runtime.runner.tick()
        assert ledger.post_calls == 0
        assert ledger.credit_count() == 0
        with runtime._uow() as work:
            case = work.cases.get(body["case_id"])
        assert case.state is CaseState.NEEDS_HUMAN_REVIEW
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.KILL_SWITCH_ACTIVE


class TestMed1AFailingTickIsCountedNotSwallowed:
    def test_failures_count_up_and_a_good_tick_resets(self) -> None:
        class Broken:
            def tick(self) -> None:
                raise RuntimeError("database is locked")

        class Fine:
            def tick(self) -> None:
                return None

        failures = 0
        for _ in range(STALLED_AFTER):
            failures = tick_once(Broken(), failures)
        assert failures == STALLED_AFTER
        assert tick_once(Fine(), failures) == 0


class TestMed2TheOpenEndpointRefusesWhatNeedsAnAuthenticatedSource:
    @pytest.mark.parametrize(
        "kind",
        [
            "OPS_FINDING_RECEIVED",
            "DISPUTE_WITHDRAWN",
            "APPROVAL_DECIDED",
            "HUMAN_TOOK_CASE",
            "HUMAN_RETURNED_CASE",
            "HUMAN_REOPENED_CASE",
            "HUMAN_CONFIRMED_WITHDRAWAL",
            "TIMER_FIRED",
        ],
    )
    def test_refused(self, kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from dispute_agent.api.runtime import OPEN_ENDPOINT

        assert InboundEventType(kind) not in OPEN_ENDPOINT
        app = running_app(tmp_path, monkeypatch, kill_switch=False)
        runtime = app.state.application.runtime
        event = type("E", (), {"type": InboundEventType(kind)})()
        with pytest.raises(PermissionError):
            runtime.submit_inbound(event)


class TestMed3AndLow1Approvers:
    def _awaiting(self, stage: Any) -> Any:
        return stage("calc_error_wrong_slab", ["dispute_raised@0h"], ["propose_credit"]).world

    def test_a_rejection_by_a_non_approver_is_not_a_decision(self, stage: Any) -> None:
        world = self._awaiting(stage)
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            [pending] = work.approvals.pending(world.case_id)
            work.approvals.decide(
                pending.id,
                ApprovalDecision.REJECTED,
                Actor(kind=ActorKind.HUMAN, id="human:someone-else"),
                world.clock.now(),
                "no",
            )
        world.drive()
        case = world.report().case
        assert case.review_reason is not None
        assert "not an approver" in (case.review_reason.detail or "")

    def test_an_expired_approval_keeps_its_own_reason(self, stage: Any) -> None:
        world = self._awaiting(stage)
        with SqliteUnitOfWork(world.engine, clock=world.clock) as work:
            [pending] = work.approvals.pending(world.case_id)
            work.approvals.decide(pending.id, ApprovalDecision.EXPIRED, None, world.clock.now(), "")
        world.drive()
        case = world.report().case
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.APPROVAL_EXPIRED

    def test_r13_denies_a_credit_approved_by_someone_not_on_the_list(self) -> None:
        """The executor's own re-check, not only ApplyApprovalStep (defence in depth)."""
        from dispute_agent.kernel.domain.enums import ApprovalSubject
        from dispute_agent.kernel.policy.rules.r13_credit_requires_approval import (
            CreditRequiresApproval,
        )
        from tests.unit.policy.test_rules import an_approval, credit_intent

        intent = credit_intent()
        approval = an_approval(
            ApprovalSubject.CREDIT,
            decided_by=Actor(kind=ActorKind.HUMAN, id="human:someone-else"),
        )
        from dispute_agent.kernel.domain.enums import PolicyStage

        result = CreditRequiresApproval().evaluate(
            ctx(stage=PolicyStage.EXECUTION, intent=intent, approval=approval)
        )
        assert result.verdict is VerdictKind.DENY
        assert result.reason == "approver_not_listed"


class TestRetriesAreOnlyWhereTheyAreSafe:
    def test_a_ledger_that_keeps_timing_out_is_tried_three_times_then_failed(
        self, stage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down(self: Any, req: Any, key: Any) -> Any:
            self.post_calls += 1
            raise TransientPortError("the ledger did not respond")

        monkeypatch.setattr(SimulatedLedger, "post_credit", down)
        world = stage(
            "calc_error_wrong_slab",
            ["dispute_raised@0h", "approve(credit)@1h"],
            ["propose_credit"],
        ).world
        assert world.ledger.post_calls == 3
        case = world.report().case
        assert case.review_reason is not None
        assert case.review_reason.code is ReviewCode.ACTION_FAILED

    def test_an_email_that_fails_transiently_is_sent_once_never_retried(
        self, stage: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def down(self: Any, msg: Any, key: Any) -> Any:
            self._send_calls += 1
            raise TransientPortError("Graph did not respond")

        monkeypatch.setattr(FakeMessageChannel, "send", down)
        world = stage(
            "kurta_box_larger",
            ["dispute_raised@0h", "approve(evidence_request)@1h"],
            ["request_seller_evidence"],
        ).world
        assert world.channel._send_calls == 1


class TestContactLimits:
    def _clarification(self) -> Any:
        intent = message_intent()
        payload = MessagePayload(
            kind=MessageKind.CLARIFICATION_REQUEST, to=SELLER_CONTACT, subject="s", body="b"
        )
        return intent.model_copy(update={"payload": payload})

    def test_a_clarification_is_exempt_from_the_daily_limit(self) -> None:
        result = ContactLimitDaily().evaluate(
            ctx(intent=self._clarification(), messages_sent_today=1)
        )
        assert result.verdict is VerdictKind.ALLOW
        assert result.reason == "clarification_exempt"

    def test_but_not_from_the_per_case_limit(self) -> None:
        result = ContactLimitCase().evaluate(
            ctx(intent=self._clarification(), messages_sent_total=3)
        )
        assert result.verdict is VerdictKind.DENY


class TestRepeatDispute:
    def test_a_repeat_on_a_credited_shipment_escalates(self) -> None:
        assert repeat_dispute_signal("case_x", already_credited=True).min_verdict is (
            VerdictKind.ESCALATE
        )

    def test_a_repeat_with_no_credit_still_needs_approval(self) -> None:
        assert repeat_dispute_signal("case_x", already_credited=False).min_verdict is (
            VerdictKind.REQUIRE_APPROVAL
        )


class TestEligibilityFromEvidence:
    @pytest.mark.parametrize("status", [None, "delivered", "IN_TRANSIT_SOMEWHERE"])
    def test_anything_but_a_known_status_is_unknown(self, status: str | None) -> None:
        from datetime import UTC, datetime

        facts = CaseFacts(
            shipment_ref="SHP-1",
            claim_ref="CLM-1",
            seller_ref="SLR-1",
            seller_contact=SELLER_CONTACT,
            raised_at=datetime(2026, 11, 4, tzinfo=UTC),
            pack_facts={} if status is None else {rules.STATUS_KEY: status},
        )
        assert rules.eligibility(facts).outcome == "unknown"

    def test_a_status_that_could_not_be_fetched_leaves_eligibility_unknown(
        self, stage: Any
    ) -> None:
        world = stage("kurta_box_larger", ["dispute_raised@0h"], ["request_seller_evidence"]).world
        world.source.faults["shipment.status"] = "timeout"  # FAILED, not FOUND
        case = world.report().case
        bare = case.model_copy(update={"facts": case.facts.model_copy(update={"pack_facts": {}})})
        facts = world.line._eligibility_facts(bare)
        assert rules.STATUS_KEY not in facts.pack_facts
        assert rules.eligibility(facts).outcome == "unknown"


_ = EvidenceRequirement
