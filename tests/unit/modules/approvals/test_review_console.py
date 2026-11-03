"""Card P3-06: the review queue, which is where a person authorises a real send.

Three tests carry the card, and all three are about things that must be impossible rather
than things that must work.

`TestTheApprovalIsBoundToTheText` — approve text A, try to send text B: refused. That is
I13, and it is enforced by comparing the hash on the approval with the hash on the intent.
Editing a draft after it was approved produces a new hash, so the approval no longer covers
it and R10 denies the send.

`TestTheReviewerComesFromTheSession` — there is no parameter for the actor. Not "a parameter
that is validated": none. The identity is fixed when the console is constructed, so a
request body has nothing to claim with (ADR 0018).

`TestARejectionReturnsTheCase` — a no carries a reason, because the reason goes back to the
case as `prior_rejections` and is shown to whatever drafts the next attempt. A rejection
with no reason produces the same message again.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
from dispute_agent.kernel.domain.actions import ApprovalRequest
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    ApprovalDecision,
    ApprovalSubject,
    MessageKind,
)
from dispute_agent.kernel.domain.errors import (
    AuthPortError,
    ConfigError,
    DomainValidationError,
)
from dispute_agent.kernel.domain.ids import (
    ActionId,
    ApprovalId,
    CaseId,
    IntentId,
    PayloadHash,
)
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.modules.approvals.store_backed.service import StoreBackedApprovalService
from dispute_agent.modules.approvals.streamlit_ui.console import (
    HUMAN_PREFIX,
    ReviewConsole,
    sign_in,
)

if TYPE_CHECKING:
    from pathlib import Path

    from dispute_agent.infra.sqlite.approval_store import SqliteApprovalStore

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
CASE = CaseId("case_review")
INTENT = IntentId("int_review")
APPROVAL = ApprovalId("apr_review")

#: Two different drafts. Their hashes differ, which is the whole mechanism.
HASH_A = PayloadHash("sha256:" + "a" * 64)
HASH_B = PayloadHash("sha256:" + "b" * 64)

REVIEWER = Actor(kind=ActorKind.HUMAN, id=f"{HUMAN_PREFIX}owner")
TOKEN = "a-long-random-review-console-token"


@pytest.fixture
def store(tmp_path: Path) -> SqliteApprovalStore:
    """A real database, with the case and intent the approval belongs to.

    Not a fake. The interesting behaviour of this store is in its WHERE clauses and the
    schema's own constraints, and an in-memory stand-in would test neither - the foreign
    keys refused the first version of this fixture, which is exactly the point.
    """
    engine = create_sqlite_engine(tmp_path / "cases.sqlite3")
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    work = SqliteUnitOfWork(engine, clock=FakeClock(NOW)).__enter__()
    _seed(work)
    return work.approvals


def _seed(work: SqliteUnitOfWork) -> None:
    """The rows an approval points at. Written directly, because building them through
    their own stores would make this fixture a test of those."""
    from dispute_agent.infra.sqlite.mapping import to_iso
    from dispute_agent.infra.sqlite.tables import action_intents, cases

    work.connection.execute(
        cases.insert().values(
            id=str(CASE),
            pack="weight_dispute",
            shipment_ref="SHP-000001",
            claim_ref="CLM-000001",
            seller_ref="SELL-0001",
            seller_contact="seller@example.invalid",
            facts_json="{}",
            # RECEIVED because the schema insists every case starts there
            # (case_must_start_received_v1). The approval does not care what state the
            # case is in, and walking it to AWAITING_APPROVAL would make this fixture a
            # test of the state machine.
            state="RECEIVED",
            version=1,
            correlation_id="corr_review",
            opened_at=to_iso(NOW),
            updated_at=to_iso(NOW),
            state_entered_at=to_iso(NOW),
        )
    )
    work.connection.execute(
        action_intents.insert().values(
            id=str(INTENT),
            case_id=str(CASE),
            source_kind="PROPOSAL",
            source_ref="prp_review",
            action_type="SEND_MESSAGE",
            message_kind=MessageKind.EVIDENCE_REQUEST.value,
            payload_json="{}",
            payload_hash=str(HASH_A),
            status="PLANNED",
            created_at=to_iso(NOW),
        )
    )


def a_request(payload_hash: PayloadHash = HASH_A, **overrides: object) -> ApprovalRequest:
    fields: dict[str, object] = {
        "approval_id": APPROVAL,
        "case_id": CASE,
        "intent_id": INTENT,
        "subject": ApprovalSubject.MESSAGE,
        "payload_hash": payload_hash,
        "message_kind": MessageKind.EVIDENCE_REQUEST,
        "requested_at": NOW,
        "expires_at": NOW + timedelta(hours=72),
    }
    fields.update(overrides)
    return ApprovalRequest(**fields)


def console(store: SqliteApprovalStore, reviewer: Actor = REVIEWER) -> ReviewConsole:
    return ReviewConsole(store=store, clock=FakeClock(NOW), reviewer=reviewer)


class TestTheQueue:
    def test_a_new_request_is_pending(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        assert [a.id for a in console(store).queue()] == [APPROVAL]

    def test_a_decided_request_leaves_the_queue(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        console(store).approve(APPROVAL)
        assert console(store).queue() == []

    def test_two_pending_approvals_for_one_intent_are_refused(
        self, store: SqliteApprovalStore
    ) -> None:
        """Two open requests for one message means two chances to say yes, and the second
        would not be bound to the text the first reviewer saw."""
        store.add(a_request())
        with pytest.raises(DomainValidationError, match="already has a pending approval"):
            store.add(a_request(approval_id=ApprovalId("apr_other")))


class TestTheApprovalIsBoundToTheText:
    """I13, and the reason this queue exists at all."""

    def test_approving_records_the_hash_that_was_shown(self, store: SqliteApprovalStore) -> None:
        store.add(a_request(HASH_A))
        approved = console(store).approve(APPROVAL)
        assert approved.request.payload_hash == HASH_A
        assert approved.decision is ApprovalDecision.APPROVED

    def test_an_approval_does_not_cover_different_text(self, store: SqliteApprovalStore) -> None:
        """Approve text A, attempt to send text B. The hashes differ, so R10 denies it -
        checked here through the rule the executor actually runs rather than by comparing
        two strings in the test."""
        from tests.unit.policy.test_r09_numbers import CASE as POLICY_CASE
        from tests.unit.policy.test_r09_numbers import intent as a_message_intent

        store.add(a_request(HASH_A))
        approved = console(store).approve(APPROVAL)

        from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind
        from dispute_agent.kernel.policy.rules import MessageApprovalBound
        from dispute_agent.kernel.policy.verdicts import PolicyContext

        edited = a_message_intent(body="A different message entirely.").model_copy(
            update={"id": INTENT, "payload_hash": HASH_B}
        )
        verdict = MessageApprovalBound().evaluate(
            PolicyContext(
                case=POLICY_CASE,
                stage=PolicyStage.EXECUTION,
                intent=edited,
                approval=approved,
                now=NOW,
            )
        )
        assert verdict.verdict is VerdictKind.DENY
        assert verdict.reason == "approval_missing_or_unbound"

    def test_the_same_text_is_allowed(self, store: SqliteApprovalStore) -> None:
        """The control. Without it the test above would pass against a rule that denied
        everything."""
        from tests.unit.policy.test_r09_numbers import CASE as POLICY_CASE
        from tests.unit.policy.test_r09_numbers import intent as a_message_intent

        store.add(a_request(HASH_A))
        approved = console(store).approve(APPROVAL)

        from dispute_agent.kernel.domain.enums import PolicyStage, VerdictKind
        from dispute_agent.kernel.policy.rules import MessageApprovalBound
        from dispute_agent.kernel.policy.verdicts import PolicyContext

        unchanged = a_message_intent().model_copy(update={"id": INTENT, "payload_hash": HASH_A})
        verdict = MessageApprovalBound().evaluate(
            PolicyContext(
                case=POLICY_CASE,
                stage=PolicyStage.EXECUTION,
                intent=unchanged,
                approval=approved,
                now=NOW,
            )
        )
        assert verdict.verdict is VerdictKind.ALLOW

    def test_the_console_never_re_renders_the_text(self) -> None:
        """Structural. A page that re-rendered the draft could show something subtly
        different from the text whose hash is about to be approved, so the console takes
        the body as a value and has no renderer of its own."""
        source = inspect.getsource(ReviewConsole)
        assert "render(" not in source.replace("render_fact(", "")


class TestOneYesIsOneSend:
    def test_an_approval_can_only_be_consumed_once(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        console(store).approve(APPROVAL)
        store.consume(APPROVAL, ActionId("act_1"))
        with pytest.raises(DomainValidationError, match="already used"):
            store.consume(APPROVAL, ActionId("act_2"))

    def test_an_unapproved_approval_cannot_be_consumed(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        with pytest.raises(DomainValidationError, match="PENDING"):
            store.consume(APPROVAL, ActionId("act_1"))

    def test_a_decision_cannot_be_made_twice(self, store: SqliteApprovalStore) -> None:
        """Two browser tabs on the same approval. The second one loses."""
        store.add(a_request())
        console(store).approve(APPROVAL)
        with pytest.raises(DomainValidationError, match="already APPROVED"):
            console(store).reject(APPROVAL, "changed my mind")

    def test_a_rejection_cannot_be_overturned_by_approving(
        self, store: SqliteApprovalStore
    ) -> None:
        store.add(a_request())
        console(store).reject(APPROVAL, "the wording is wrong")
        with pytest.raises(DomainValidationError, match="already REJECTED"):
            console(store).approve(APPROVAL)


class TestTheReviewerComesFromTheSession:
    """ADR 0018, made structural."""

    def test_there_is_no_parameter_for_the_actor(self) -> None:
        """Not a validated parameter - none. A request body has nothing to claim with."""
        for method in (ReviewConsole.approve, ReviewConsole.reject):
            parameters = set(inspect.signature(method).parameters) - {"self"}
            for suspicious in ("actor", "by", "decided_by", "user", "reviewer", "approver"):
                assert suspicious not in parameters, f"{method.__name__} takes {suspicious}"

    def test_the_decision_records_the_session_identity(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        approved = console(store).approve(APPROVAL)
        assert approved.decided_by == REVIEWER
        assert approved.decided_at == NOW

    def test_a_system_actor_cannot_run_the_console(self, store: SqliteApprovalStore) -> None:
        """A yes recorded against "system" is a yes nobody gave."""
        with pytest.raises(DomainValidationError, match="human"):
            console(store, Actor(kind=ActorKind.SYSTEM, id="system"))

    def test_an_actor_id_without_the_human_prefix_is_refused(
        self, store: SqliteApprovalStore
    ) -> None:
        """R10 checks the same prefix before allowing a send, so an id in another shape
        would produce an approval that can never authorise anything - failing here says so
        immediately instead."""
        with pytest.raises(DomainValidationError, match="human:"):
            console(store, Actor(kind=ActorKind.HUMAN, id="owner"))

    def test_the_store_itself_refuses_a_non_human_decision(
        self, store: SqliteApprovalStore
    ) -> None:
        """Belt and braces: even reaching past the console does not help."""
        store.add(a_request())
        with pytest.raises(DomainValidationError, match="human decider"):
            store.decide(
                APPROVAL,
                ApprovalDecision.APPROVED,
                Actor(kind=ActorKind.SYSTEM, id="system"),
                NOW,
                "",
            )


class TestSigningIn:
    def test_the_right_token_establishes_a_human_reviewer(self) -> None:
        actor = sign_in(TOKEN, TOKEN, "owner")
        assert actor.kind is ActorKind.HUMAN
        assert actor.id == f"{HUMAN_PREFIX}owner"

    def test_the_wrong_token_is_refused(self) -> None:
        with pytest.raises(AuthPortError):
            sign_in("not-the-token", TOKEN, "owner")

    def test_no_token_is_refused(self) -> None:
        with pytest.raises(AuthPortError):
            sign_in(None, TOKEN, "owner")

    def test_an_unconfigured_console_refuses_to_run(self) -> None:
        """Not "allow everything when no token is set". The queue authorises real sends."""
        with pytest.raises(ConfigError, match="REVIEW_CONSOLE_TOKEN"):
            sign_in(TOKEN, None, "owner")

    def test_the_comparison_is_constant_time(self) -> None:
        """Far-fetched locally, and the careless version is what gets copied next."""
        source = inspect.getsource(sign_in)
        assert "compare_digest" in source
        assert "provided == expected" not in source


class TestARejectionReturnsTheCase:
    def test_a_rejection_records_its_reason(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        rejected = console(store).reject(APPROVAL, "too blunt, soften the opening")
        assert rejected.decision is ApprovalDecision.REJECTED
        assert rejected.note == "too blunt, soften the opening"

    def test_a_rejection_without_a_reason_is_refused(self, store: SqliteApprovalStore) -> None:
        """The reason is not bookkeeping: it goes back to the case as `prior_rejections`,
        and without it the next attempt produces the same message."""
        store.add(a_request())
        with pytest.raises(DomainValidationError, match="reason"):
            console(store).reject(APPROVAL, "   ")

    def test_a_rejected_approval_cannot_authorise_a_send(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        console(store).reject(APPROVAL, "wrong tone")
        with pytest.raises(DomainValidationError):
            store.consume(APPROVAL, ActionId("act_1"))


class TestTheConsoleCannotSend:
    def test_it_holds_no_channel_or_executor(self, store: SqliteApprovalStore) -> None:
        """A console that could send would be a second path to the outside world, missing
        the executor and everything it guarantees (I2)."""
        held = set(vars(console(store)))
        for forbidden in ("channel", "executor", "port", "email", "ticketing"):
            assert forbidden not in held

    def test_approving_writes_a_row_and_nothing_else(self, store: SqliteApprovalStore) -> None:
        store.add(a_request())
        approved = console(store).approve(APPROVAL)
        assert approved.used_by_action_id is None


class TestTheServiceOnlyNotifies:
    def test_requesting_marks_the_approval_notified(self, store: SqliteApprovalStore) -> None:
        approval = store.add(a_request())
        assert approval.notified_at is None

        StoreBackedApprovalService(store=store, clock=FakeClock(NOW)).request(approval)
        assert store.get(APPROVAL).notified_at == NOW

    def test_notifying_twice_is_not_an_error(self, store: SqliteApprovalStore) -> None:
        """The runner retries until it succeeds; raising on the second attempt would turn
        a successful notification into a permanent failure."""
        approval = store.add(a_request())
        service = StoreBackedApprovalService(store=store, clock=FakeClock(NOW))
        service.request(approval)
        service.request(approval)

    def test_it_has_no_way_to_return_a_decision(self) -> None:
        """ADR 0013. A port that could return one would be a second source of truth about
        who said yes, and the easiest one to forge."""
        for name, method in inspect.getmembers(
            StoreBackedApprovalService, predicate=inspect.isfunction
        ):
            if name.startswith("_"):
                continue
            annotation = inspect.signature(method).return_annotation
            assert annotation in (None, "None"), f"{name} returns {annotation}"

    def test_withdrawing_something_already_gone_is_not_an_error(
        self, store: SqliteApprovalStore
    ) -> None:
        service = StoreBackedApprovalService(store=store, clock=FakeClock(NOW))
        service.withdraw(ApprovalId("apr_never_existed"), "superseded")

    def test_unnotified_lists_what_the_runner_owes(self, store: SqliteApprovalStore) -> None:
        approval = store.add(a_request())
        assert [a.id for a in store.unnotified()] == [APPROVAL]
        StoreBackedApprovalService(store=store, clock=FakeClock(NOW)).request(approval)
        assert store.unnotified() == []
