"""Cards P1-06 and P1-07: one test per implemented rule, and the composition law.

The rules Phase 1 cannot implement have skipped tests naming the phase that writes them, so
the catalogue's gaps show up in a test run rather than only in a document.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.actions import (
    ActionIntent,
    Approval,
    ApprovalRequest,
    CreditPayload,
    IssuePayload,
    MessagePayload,
    RuleResult,
)
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionType,
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    CaseState,
    IntentSource,
    IntentStatus,
    MessageKind,
    PolicyStage,
    ReviewCode,
    VerdictKind,
)
from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.ids import (
    ApprovalId,
    CaseId,
    CorrelationId,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Actor, Case
from dispute_agent.kernel.domain.patterns import CautionSignal
from dispute_agent.kernel.domain.values import Money
from dispute_agent.kernel.policy.config import MessagesConfig, PolicyConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import MANDATORY_RULES, phase_1_rules
from dispute_agent.kernel.policy.rules.r02_kill_switch import KillSwitchRule
from dispute_agent.kernel.policy.rules.r03_module_available import ModuleAvailable
from dispute_agent.kernel.policy.rules.r04_contact_limit_case import ContactLimitCase
from dispute_agent.kernel.policy.rules.r05_contact_limit_daily import ContactLimitDaily
from dispute_agent.kernel.policy.rules.r06_recipient_allowlisted import RecipientAllowlisted
from dispute_agent.kernel.policy.rules.r08_message_review_required import MessageReviewRequired
from dispute_agent.kernel.policy.rules.r10_message_approval_bound import MessageApprovalBound
from dispute_agent.kernel.policy.rules.r13_credit_requires_approval import CreditRequiresApproval
from dispute_agent.kernel.policy.rules.r17_credit_not_duplicate import CreditNotDuplicate
from dispute_agent.kernel.policy.signals import floor_of, raise_to_floor
from dispute_agent.kernel.policy.verdicts import PolicyContext, combine

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)
LATER = NOW + timedelta(hours=72)
HASH = PayloadHash("sha256:" + "a" * 64)
SELLER = "seller@example.invalid"
HUMAN = Actor(kind=ActorKind.HUMAN, id="human:owner")

FACTS = CaseFacts(
    shipment_ref="SHP-0001",
    claim_ref="CHG-0001",
    seller_ref="SELLER-0001",
    seller_contact=SELLER,
    raised_at=NOW,
)
CASE = Case(
    id=CaseId("case_01HQ"),
    pack="weight_dispute",
    facts=FACTS,
    state=CaseState.INVESTIGATING,
    version=1,
    correlation_id=CorrelationId("corr_01HQ"),
    opened_at=NOW,
    updated_at=NOW,
    state_entered_at=NOW,
)
POLICY = PolicyConfig(messages=MessagesConfig(allowed_recipients=frozenset({SELLER})))
ALL_MODULES = dict.fromkeys(
    ("email", "ticketing", "ledger", "approvals", "evidence", "reasoning"), True
)


def message_intent(**overrides: object) -> ActionIntent:
    payload = MessagePayload(kind=MessageKind.EVIDENCE_REQUEST, to=SELLER, subject="s", body="b")
    fields: dict[str, object] = {
        "id": IntentId("int_01HQ"),
        "case_id": CASE.id,
        "source_kind": IntentSource.PROPOSAL,
        "source_ref": "prp_01HQ",
        "action_type": ActionType.SEND_MESSAGE,
        "payload": payload,
        "payload_hash": HASH,
        "status": IntentStatus.PLANNED,
        "created_at": NOW,
    }
    fields.update(overrides)
    return ActionIntent(**fields)


def credit_intent(amount: str = "1820.00", **overrides: object) -> ActionIntent:
    payload = CreditPayload(
        shipment_ref="SHP-0001", amount=Money.of(amount), reason_code="calculation_error"
    )
    fields: dict[str, object] = {
        "id": IntentId("int_01HQ"),
        "case_id": CASE.id,
        "source_kind": IntentSource.PROPOSAL,
        "source_ref": "prp_01HQ",
        "action_type": ActionType.POST_CREDIT,
        "payload": payload,
        "payload_hash": HASH,
        "status": IntentStatus.PLANNED,
        "created_at": NOW,
    }
    fields.update(overrides)
    return ActionIntent(**fields)


def an_approval(subject: ApprovalSubject = ApprovalSubject.MESSAGE, **overrides: object):
    request = ApprovalRequest(
        approval_id=ApprovalId("apr_01HQ"),
        case_id=CASE.id,
        intent_id=IntentId("int_01HQ"),
        subject=subject,
        payload_hash=HASH,
        amount=Money.of("1820.00") if subject is ApprovalSubject.CREDIT else None,
        message_kind=MessageKind.EVIDENCE_REQUEST if subject is ApprovalSubject.MESSAGE else None,
        requested_at=NOW,
        expires_at=LATER,
    )
    fields: dict[str, object] = {
        "id": ApprovalId("apr_01HQ"),
        "request": request,
        "decision": ApprovalDecision.APPROVED,
        "decided_by": HUMAN,
        "decided_at": NOW,
    }
    fields.update(overrides)
    return Approval(**fields)


def ctx(**overrides: object) -> PolicyContext:
    fields: dict[str, object] = {
        "case": CASE,
        "stage": PolicyStage.PLANNING,
        "modules": ALL_MODULES,
        "policy": POLICY,
        "now": NOW,
    }
    fields.update(overrides)
    return PolicyContext(**fields)


class TestR02KillSwitch:
    def test_it_denies_when_engaged(self) -> None:
        result = KillSwitchRule().evaluate(ctx(kill_switch=True))
        assert result.verdict is VerdictKind.DENY
        assert result.reason == "kill_switch_active"

    def test_it_allows_when_not(self) -> None:
        assert KillSwitchRule().evaluate(ctx()).verdict is VerdictKind.ALLOW


class TestR03ModuleAvailability:
    def test_a_message_needs_email(self) -> None:
        modules = dict(ALL_MODULES, email=False)
        result = ModuleAvailable().evaluate(ctx(intent=message_intent(), modules=modules))
        assert result.verdict is VerdictKind.DENY
        assert result.reason == "module_unavailable:email"

    def test_the_reason_names_the_module(self) -> None:
        """I6: a reason a person cannot act on is barely a reason."""
        modules = dict(ALL_MODULES, ticketing=False)
        intent = message_intent(
            action_type=ActionType.UPSERT_ISSUE,
            payload=IssuePayload(summary="remeasure", description="please remeasure"),
        )
        result = ModuleAvailable().evaluate(ctx(intent=intent, modules=modules))
        assert result.reason == "module_unavailable:ticketing"

    def test_a_credit_also_needs_approvals(self) -> None:
        """Without it the case waits for a review nobody will ever be asked for (I3)."""
        modules = dict(ALL_MODULES, approvals=False)
        result = ModuleAvailable().evaluate(ctx(intent=credit_intent(), modules=modules))
        assert result.reason == "module_unavailable:approvals"

    def test_an_unknown_module_is_treated_as_off(self) -> None:
        """Unknown is not available; defaulting the other way hides a missing module."""
        result = ModuleAvailable().evaluate(ctx(intent=message_intent(), modules={}))
        assert result.verdict is VerdictKind.DENY


class TestR04AndR05ContactLimits:
    def test_the_case_limit_denies_at_the_threshold(self) -> None:
        result = ContactLimitCase().evaluate(ctx(intent=message_intent(), messages_sent_total=3))
        assert result.reason == "contact_limit_case"

    def test_below_the_threshold_is_allowed(self) -> None:
        result = ContactLimitCase().evaluate(ctx(intent=message_intent(), messages_sent_total=2))
        assert result.verdict is VerdictKind.ALLOW

    def test_the_daily_limit_denies(self) -> None:
        result = ContactLimitDaily().evaluate(ctx(intent=message_intent(), messages_sent_today=1))
        assert result.reason == "contact_limit_daily"

    def test_neither_applies_to_a_credit(self) -> None:
        assert (
            ContactLimitCase().evaluate(ctx(intent=credit_intent(), messages_sent_total=99)).verdict
            is VerdictKind.ALLOW
        )


class TestR06RecipientAllowlisted:
    def test_an_unlisted_recipient_is_denied(self) -> None:
        intent = message_intent(
            payload=MessagePayload(
                kind=MessageKind.EVIDENCE_REQUEST, to="stranger@example.com", subject="s", body="b"
            )
        )
        result = RecipientAllowlisted().evaluate(ctx(intent=intent))
        assert result.reason == "recipient_not_allowlisted"

    def test_a_recipient_that_is_not_the_case_seller_is_denied(self) -> None:
        """A recipient the system invented is one nobody authorised, however allowlisted."""
        other = "other@example.invalid"
        policy = PolicyConfig(
            messages=MessagesConfig(allowed_recipients=frozenset({SELLER, other}))
        )
        intent = message_intent(
            payload=MessagePayload(
                kind=MessageKind.EVIDENCE_REQUEST, to=other, subject="s", body="b"
            )
        )
        result = RecipientAllowlisted().evaluate(ctx(intent=intent, policy=policy))
        assert result.reason == "recipient_mismatch"

    def test_the_case_seller_is_allowed(self) -> None:
        assert (
            RecipientAllowlisted().evaluate(ctx(intent=message_intent())).verdict
            is VerdictKind.ALLOW
        )


class TestR08MessageReviewRequired:
    def test_every_kind_is_reviewed_in_the_pilot(self) -> None:
        """D6. Each kind, so adding one later does not quietly become auto-send."""
        for kind in MessageKind:
            intent = message_intent(
                payload=MessagePayload(kind=kind, to=SELLER, subject="s", body="b")
            )
            result = MessageReviewRequired().evaluate(ctx(intent=intent))
            assert result.verdict is VerdictKind.REQUIRE_APPROVAL, kind

    def test_a_kind_moved_to_auto_send_is_allowed(self) -> None:
        policy = PolicyConfig(
            messages=MessagesConfig(
                allowed_recipients=frozenset({SELLER}),
                review_required_kinds=frozenset(MessageKind) - {MessageKind.REMINDER},
                auto_send_kinds=frozenset({MessageKind.REMINDER}),
            )
        )
        intent = message_intent(
            payload=MessagePayload(kind=MessageKind.REMINDER, to=SELLER, subject="s", body="b")
        )
        assert (
            MessageReviewRequired().evaluate(ctx(intent=intent, policy=policy)).verdict
            is VerdictKind.ALLOW
        )


class TestR10AndR13ApprovalBinding:
    def test_a_credit_always_needs_approval_at_planning(self) -> None:
        """D30: no amount is small enough to skip it."""
        result = CreditRequiresApproval().evaluate(ctx(intent=credit_intent("1.00")))
        assert result.verdict is VerdictKind.REQUIRE_APPROVAL

    def test_execution_without_an_approval_is_denied(self) -> None:
        result = CreditRequiresApproval().evaluate(
            ctx(intent=credit_intent(), stage=PolicyStage.EXECUTION)
        )
        assert result.reason == "approval_missing_or_unbound"

    def test_a_matching_approval_is_allowed(self) -> None:
        result = CreditRequiresApproval().evaluate(
            ctx(
                intent=credit_intent(),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(ApprovalSubject.CREDIT),
            )
        )
        assert result.verdict is VerdictKind.ALLOW

    def test_a_different_amount_is_denied(self) -> None:
        """An approval is a person agreeing to a number; the number must not change after."""
        result = CreditRequiresApproval().evaluate(
            ctx(
                intent=credit_intent("9999.00"),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(ApprovalSubject.CREDIT),
            )
        )
        assert result.reason == "credit_amount_mismatch"

    def test_an_approval_decided_by_the_system_is_denied(self) -> None:
        system = Actor(kind=ActorKind.SYSTEM, id="system")
        result = CreditRequiresApproval().evaluate(
            ctx(
                intent=credit_intent(),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(ApprovalSubject.CREDIT, decided_by=system),
            )
        )
        assert result.reason == "approval_missing_or_unbound"

    def test_a_used_approval_is_denied(self) -> None:
        """Single use, or one yes becomes two sends."""
        from dispute_agent.kernel.domain.ids import ActionId

        result = CreditRequiresApproval().evaluate(
            ctx(
                intent=credit_intent(),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(
                    ApprovalSubject.CREDIT, used_by_action_id=ActionId("act_01HQ")
                ),
            )
        )
        assert result.reason == "approval_missing_or_unbound"

    def test_an_expired_approval_is_stale(self) -> None:
        result = CreditRequiresApproval().evaluate(
            ctx(
                intent=credit_intent(),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(ApprovalSubject.CREDIT),
                now=LATER + timedelta(hours=1),
            )
        )
        assert result.reason == "approval_stale"

    def test_a_message_needs_its_text_approved(self) -> None:
        """I13: the text sent must be the text approved, which is the hash comparison."""
        other_hash = PayloadHash("sha256:" + "f" * 64)
        result = MessageApprovalBound().evaluate(
            ctx(
                intent=message_intent(payload_hash=other_hash),
                stage=PolicyStage.EXECUTION,
                approval=an_approval(),
            )
        )
        assert result.reason == "approval_missing_or_unbound"

    def test_a_matching_message_approval_is_allowed(self) -> None:
        result = MessageApprovalBound().evaluate(
            ctx(intent=message_intent(), stage=PolicyStage.EXECUTION, approval=an_approval())
        )
        assert result.verdict is VerdictKind.ALLOW


class TestR17CreditNotDuplicate:
    def test_a_live_credit_elsewhere_denies(self) -> None:
        """Across all cases, not per case."""
        result = CreditNotDuplicate().evaluate(
            ctx(intent=credit_intent(), live_credits_same_shipment_reason=1)
        )
        assert result.reason == "duplicate_credit"

    def test_a_case_that_already_credited_denies(self) -> None:
        result = CreditNotDuplicate().evaluate(
            ctx(intent=credit_intent(), case_has_verified_credit=True)
        )
        assert result.reason == "credit_already_posted"


class TestTheEngine:
    def engine(self) -> PolicyEngine:
        return PolicyEngine(
            phase_1_rules(), FakeClock(NOW), FakeIdGenerator(), rules_version="test"
        )

    def test_the_strictest_wins(self) -> None:
        verdict = self.engine().evaluate(ctx(intent=message_intent(), kill_switch=True))
        assert verdict.verdict is VerdictKind.DENY

    def test_every_rule_is_recorded_not_just_the_first_failure(self) -> None:
        """A person deciding what to do needs all the reasons, not the earliest one."""
        verdict = self.engine().evaluate(ctx(intent=message_intent(), kill_switch=True))
        assert len(verdict.results) == len(phase_1_rules())

    def test_escalation_sets_a_review_reason(self) -> None:
        verdict = self.engine().evaluate(ctx(intent=message_intent(), kill_switch=True))
        assert verdict.review_reason is not None
        assert verdict.review_reason.code is ReviewCode.KILL_SWITCH_ACTIVE

    def test_a_module_reason_carries_the_module(self) -> None:
        verdict = self.engine().evaluate(
            ctx(intent=message_intent(), modules=dict(ALL_MODULES, email=False))
        )
        assert verdict.review_reason is not None
        assert verdict.review_reason.module == "email"
        assert verdict.review_reason.render() == "module_unavailable:email"

    def test_a_buggy_rule_denies_rather_than_being_skipped(self) -> None:
        """Fail safe. A rule that is skipped means the system acted without a check it
        believed it had."""

        class Exploding:
            rule_id = "R99"

            def evaluate(self, ctx: PolicyContext) -> RuleResult:
                raise RuntimeError("boom")

        engine = PolicyEngine([*phase_1_rules(), Exploding()], FakeClock(NOW), FakeIdGenerator())
        verdict = engine.evaluate(ctx(intent=message_intent()))
        assert verdict.verdict is VerdictKind.DENY
        assert any(r.reason == "rule_error:R99" for r in verdict.results)

    def test_it_refuses_to_run_without_a_mandatory_rule(self) -> None:
        """Their absence would not look like a failure, which is why it is checked here."""
        without_kill_switch = [r for r in phase_1_rules() if r.rule_id != "R02"]  # type: ignore[attr-defined]
        with pytest.raises(ConfigError, match="R02"):
            PolicyEngine(without_kill_switch, FakeClock(NOW), FakeIdGenerator())

    def test_every_mandatory_rule_is_implemented_in_phase_1(self) -> None:
        assert {r.rule_id for r in phase_1_rules()} >= MANDATORY_RULES  # type: ignore[attr-defined]

    def test_the_verdict_records_the_rules_version(self) -> None:
        """Otherwise "why did this need approval?" is unanswerable after a config edit."""
        verdict = self.engine().evaluate(ctx())
        assert verdict.rules_version == "test"


class TestCautionSignalsOnlyRaise:
    """Card P1-07. I7 and I15, as a property rather than an example."""

    @given(
        base=st.sampled_from(list(VerdictKind)),
        floors=st.lists(
            st.sampled_from([VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE]),
            max_size=6,
        ),
    )
    def test_the_result_is_never_lower_than_without_the_signals(
        self, base: VerdictKind, floors: list[VerdictKind]
    ) -> None:
        signals = tuple(
            CautionSignal(
                kind="pattern_match", min_verdict=floor, reason="r", source_ref="pat_01HQ"
            )
            for floor in floors
        )
        assert raise_to_floor(base, signals) >= base

    @given(
        floors=st.lists(
            st.sampled_from([VerdictKind.REQUIRE_APPROVAL, VerdictKind.ESCALATE]),
            min_size=1,
            max_size=6,
        )
    )
    def test_order_does_not_matter(self, floors: list[VerdictKind]) -> None:
        """Taking the maximum means a set of signals has one answer however they arrive."""
        import random

        signals = [
            CautionSignal(kind="pattern_match", min_verdict=f, reason="r", source_ref=f"pat_{i}")
            for i, f in enumerate(floors)
        ]
        shuffled = list(signals)
        random.Random(1).shuffle(shuffled)
        assert floor_of(signals) == floor_of(shuffled)

    def test_a_signal_cannot_lower_a_deny(self) -> None:
        signal = CautionSignal(
            kind="pattern_match",
            min_verdict=VerdictKind.REQUIRE_APPROVAL,
            reason="r",
            source_ref="pat_01HQ",
        )
        assert raise_to_floor(VerdictKind.DENY, (signal,)) is VerdictKind.DENY

    def test_there_is_no_lowering_helper(self) -> None:
        """Structural: the module offers no way to express one."""
        import dispute_agent.kernel.policy.signals as module

        names = [n for n in dir(module) if not n.startswith("_")]
        assert not [n for n in names if "lower" in n or "clear" in n or "relax" in n]

    def test_the_engine_honours_a_signal_floor(self) -> None:
        signal = CautionSignal(
            kind="screen_flag",
            min_verdict=VerdictKind.ESCALATE,
            reason="screen_unavailable",
            source_ref="input_screen",
        )
        engine = PolicyEngine(phase_1_rules(), FakeClock(NOW), FakeIdGenerator())
        verdict = engine.evaluate(ctx(signals=(signal,)))
        assert verdict.verdict is VerdictKind.ESCALATE
        assert verdict.review_reason is not None

    def test_combine_of_nothing_is_allow(self) -> None:
        assert combine([]) is VerdictKind.ALLOW


class TestRulesNotYetImplemented:
    """Each names the phase that writes it, so the gaps are visible in a test run."""

    @pytest.mark.skip(reason="R07 recipient_mismatch in full: Phase 3 (card P3-05)")
    def test_r07_recipient_mismatch(self) -> None: ...

    @pytest.mark.skip(reason="R09 numbers_unverified needs an assessment: Phase 2 (P2-06)")
    def test_r09_numbers_unverified(self) -> None: ...

    @pytest.mark.skip(reason="R11 draft_blocked needs a drafter: Phase 4 (card P4-06)")
    def test_r11_draft_blocked(self) -> None: ...

    @pytest.mark.skip(reason="R12 reply_unresolved needs an interpreter: Phase 4 (P4-06)")
    def test_r12_reply_unresolved(self) -> None: ...

    @pytest.mark.skip(reason="R14 credit_reason_unproven needs an assessment: Phase 2 (P2-06)")
    def test_r14_credit_reason_unproven(self) -> None: ...

    @pytest.mark.skip(reason="R15 credit_amount_mismatch needs an assessment: Phase 2 (P2-06)")
    def test_r15_credit_amount_mismatch(self) -> None: ...

    @pytest.mark.skip(reason="R16 credit_exceeds_outstanding needs the ledger: Phase 5 (P5-03)")
    def test_r16_credit_exceeds_outstanding(self) -> None: ...

    @pytest.mark.skip(reason="R18 credit_already_posted follow-ups: Phase 5 (card P5-04)")
    def test_r18_credit_already_posted(self) -> None: ...

    @pytest.mark.skip(reason="R19 wait_without_expectation needs proposals: Phase 2 (P2-07)")
    def test_r19_wait_without_expectation(self) -> None: ...

    @pytest.mark.skip(reason="R20 seller_withdrawal_claimed needs replies: Phase 3 (P3-07)")
    def test_r20_seller_withdrawal_claimed(self) -> None: ...

    @pytest.mark.skip(reason="R21 manipulation_suspected needs the screen: Phase 4 (P4-08)")
    def test_r21_manipulation_suspected(self) -> None: ...

    @pytest.mark.skip(reason="R22 low_confidence needs a reasoner: Phase 4 (card P4-05)")
    def test_r22_low_confidence(self) -> None: ...

    @pytest.mark.skip(reason="R23 guardrail_blocked needs Foundry: Phase 4b (card P4B-05)")
    def test_r23_guardrail_blocked(self) -> None: ...

    @pytest.mark.skip(reason="R24/R25 approval staleness in full: Phase 5 (card P5-05)")
    def test_r24_and_r25(self) -> None: ...
