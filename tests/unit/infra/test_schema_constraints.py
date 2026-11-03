"""Card P1-01: the schema refuses what it is supposed to refuse.

The constraints in `schema.sql` are not belt-and-braces for the Python checks — they are the
last line that holds when a Python check is deleted, bypassed or never written. Several
invariants are stated there and nowhere else yet: I3's "no credit without an approval" is a
CHECK and a trigger, not an `if`.

Each test starts from a row the database accepts, changes one thing, and asserts the refusal.
That shape matters: a row broken in two ways can be refused by the wrong constraint and keep
passing after the one under test is removed.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from tests.unit.infra.conftest import (
    HASH,
    KEY,
    LATER,
    TS,
    a_case,
    a_timer,
    an_action,
    an_approval,
    an_event,
    an_intent,
    insert,
)


def refuses(db: sqlite3.Connection, match: str | None = None) -> Any:
    """Any refusal from the database: a CHECK, a UNIQUE index, a foreign key or a trigger."""
    errors = (sqlite3.IntegrityError, sqlite3.OperationalError)
    return pytest.raises(errors) if match is None else pytest.raises(errors, match=match)


class TestForeignKeysAreReallyOn:
    """The card's warning: a silently ignored pragma makes later tests pass for nothing."""

    def test_a_bad_foreign_key_is_refused(self, db: sqlite3.Connection) -> None:
        with refuses(db, "FOREIGN KEY"):
            insert(db, "events", an_event(case_id="case_does_not_exist"))

    def test_the_pragma_is_reported_as_on(self, db: sqlite3.Connection) -> None:
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


class TestCases:
    def test_a_case_must_start_in_received_at_version_one(self, db: sqlite3.Connection) -> None:
        with refuses(db, "case_must_start_received_v1"):
            insert(db, "cases", a_case(state="INVESTIGATING", closed_at=None))

    def test_closed_at_is_set_exactly_for_a_terminal_state(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            db.execute("UPDATE cases SET state='WITHDRAWN', version=2 WHERE id='case_01HQ'")

    def test_a_review_reason_exists_exactly_in_human_review(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            db.execute(
                "UPDATE cases SET state='NEEDS_HUMAN_REVIEW', version=2 WHERE id='case_01HQ'"
            )
        db.execute(
            "UPDATE cases SET state='NEEDS_HUMAN_REVIEW', review_code='step_limit', version=2 "
            "WHERE id='case_01HQ'"
        )

    def test_module_unavailable_must_name_the_module(self, db: sqlite3.Connection) -> None:
        """I6: a reason a human cannot act on is barely a reason."""
        insert(db, "cases", a_case())
        with refuses(db):
            db.execute(
                "UPDATE cases SET state='NEEDS_HUMAN_REVIEW', review_code='module_unavailable', "
                "version=2 WHERE id='case_01HQ'"
            )

    def test_the_version_must_increment_by_exactly_one(self, db: sqlite3.Connection) -> None:
        """Optimistic concurrency only works if nothing can skip a version."""
        insert(db, "cases", a_case())
        with refuses(db, "version_must_increment_by_one"):
            db.execute("UPDATE cases SET version=5 WHERE id='case_01HQ'")

    def test_an_illegal_state_change_is_refused(self, db: sqlite3.Connection) -> None:
        """I8, in the database. A write that bypassed the state machine still fails."""
        insert(db, "cases", a_case())
        with refuses(db, "illegal_transition"):
            db.execute(
                "UPDATE cases SET state='RESOLVED_CREDITED', closed_at=?, version=2 "
                "WHERE id='case_01HQ'",
                (TS,),
            )

    def test_a_legal_state_change_is_allowed(self, db: sqlite3.Connection) -> None:
        """The positive control: the trigger refuses the illegal, not everything."""
        insert(db, "cases", a_case())
        db.execute("UPDATE cases SET state='GATHERING_EVIDENCE', version=2 WHERE id='case_01HQ'")
        assert db.execute("SELECT state FROM cases").fetchone()[0] == "GATHERING_EVIDENCE"

    def test_a_case_is_never_deleted(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db, "cases_are_never_deleted"):
            db.execute("DELETE FROM cases WHERE id='case_01HQ'")

    def test_updated_at_cannot_precede_opened_at(self, db: sqlite3.Connection) -> None:
        with refuses(db):
            insert(db, "cases", a_case(updated_at="2026-02-01T00:00:00.000000Z"))

    def test_one_open_case_per_shipment_and_claim(self, db: sqlite3.Connection) -> None:
        """Intake deduplication is a database guarantee, not a check with a race in it."""
        insert(db, "cases", a_case())
        with refuses(db, "UNIQUE"):
            insert(db, "cases", a_case(id="case_02HQ"))

    def test_a_closed_case_frees_the_slot(self, db: sqlite3.Connection) -> None:
        """The index is partial: only open cases occupy the pair."""
        insert(db, "cases", a_case())
        db.execute(
            "UPDATE cases SET state='REJECTED_INELIGIBLE', closed_at=?, version=2 "
            "WHERE id='case_01HQ'",
            (TS,),
        )
        insert(db, "cases", a_case(id="case_02HQ"))


class TestEventsAreAppendOnly:
    """I8. There is no update path and no delete path, in the port or in the database."""

    def test_an_event_cannot_be_updated(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "events", an_event())
        with refuses(db, "events_are_append_only"):
            db.execute("UPDATE events SET payload_json='{\"tampered\":1}' WHERE id='evt_01HQ'")

    def test_an_event_cannot_be_deleted(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "events", an_event())
        with refuses(db, "events_are_append_only"):
            db.execute("DELETE FROM events WHERE id='evt_01HQ'")

    def test_a_sequence_number_cannot_repeat(self, db: sqlite3.Connection) -> None:
        """UNIQUE(case_id, seq): a race fails loudly instead of reordering the diary."""
        insert(db, "cases", a_case())
        insert(db, "events", an_event())
        with refuses(db, "UNIQUE"):
            insert(db, "events", an_event(id="evt_02HQ", dedupe_key="dispute_api:form-2"))

    def test_an_input_event_must_carry_a_dedupe_key(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(db, "events", an_event(dedupe_key=None))

    def test_an_internal_event_must_not(self, db: sqlite3.Connection) -> None:
        """Internal events are unique by (case_id, seq) and by their transaction."""
        insert(db, "cases", a_case())
        insert(db, "events", an_event())
        with refuses(db):
            insert(
                db,
                "events",
                an_event(
                    id="evt_02HQ",
                    seq=2,
                    category="INTERNAL",
                    type="STATE_CHANGED",
                    to_state="GATHERING_EVIDENCE",
                    causation_id="evt_01HQ",
                    dedupe_key="should-not-be-here",
                ),
            )

    def test_a_repeated_dedupe_key_is_refused(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "events", an_event())
        with refuses(db, "UNIQUE"):
            insert(db, "events", an_event(id="evt_02HQ", seq=2))

    def test_a_state_change_must_name_the_state(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(
                db,
                "events",
                an_event(category="INTERNAL", type="STATE_CHANGED", dedupe_key=None),
            )


class TestInbox:
    def test_the_dedupe_key_must_match_its_parts(self, db: sqlite3.Connection) -> None:
        """Otherwise two rows could claim different identities for the same source event."""
        with refuses(db):
            insert(
                db,
                "inbox",
                {
                    "id": "inb_01HQ",
                    "dedupe_key": "something-else",
                    "source": "dispute_api",
                    "source_event_id": "form-1",
                    "event_type": "DISPUTE_RAISED",
                    "event_json": "{}",
                    "received_at": TS,
                    "status": "PENDING",
                },
            )


class TestEvidenceIsNeverInvented:
    def test_a_value_exists_only_when_found(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(
                db,
                "evidence_items",
                {
                    "id": "evi_01HQ",
                    "case_id": "case_01HQ",
                    "gather_round": 1,
                    "requirement_key": "shipment.measured_dims",
                    "source": "shipment_api",
                    "provenance": "SYSTEM",
                    "status": "MISSING",
                    "value_json": '{"length_cm": 30}',
                    "retrieved_at": TS,
                    "content_hash": HASH,
                },
            )

    def test_a_failure_must_carry_a_code(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(
                db,
                "evidence_items",
                {
                    "id": "evi_01HQ",
                    "case_id": "case_01HQ",
                    "gather_round": 1,
                    "requirement_key": "shipment.measured_dims",
                    "source": "shipment_api",
                    "provenance": "SYSTEM",
                    "status": "FAILED",
                    "retrieved_at": TS,
                    "content_hash": HASH,
                },
            )


class TestCreditsNeedApprovals:
    """I3, stated in the schema so it holds even if every Python check were removed."""

    def setup_case(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(
            db,
            "action_intents",
            an_intent(
                action_type="POST_CREDIT",
                message_kind=None,
                amount_paise=182000,
                reason_code="calculation_error",
            ),
        )

    def test_a_credit_action_without_an_approval_is_refused(self, db: sqlite3.Connection) -> None:
        self.setup_case(db)
        with refuses(db):
            insert(
                db,
                "actions",
                an_action(
                    action_type="POST_CREDIT",
                    credit_shipment_ref="SHP-0001",
                    credit_reason_code="calculation_error",
                ),
            )

    def test_an_approval_that_does_not_cover_the_action_is_refused(
        self, db: sqlite3.Connection
    ) -> None:
        """tr_actions_approval_binding: same intent, same hash, APPROVED by a human, unused."""
        self.setup_case(db)
        insert(
            db,
            "approvals",
            an_approval(
                subject="CREDIT",
                message_kind=None,
                amount_paise=182000,
                payload_hash="sha256:" + "f" * 64,
            ),
        )
        with refuses(db, "approval_does_not_cover_action"):
            insert(
                db,
                "actions",
                an_action(
                    action_type="POST_CREDIT",
                    approval_id="apr_01HQ",
                    credit_shipment_ref="SHP-0001",
                    credit_reason_code="calculation_error",
                ),
            )

    def test_an_approval_not_decided_by_a_human_is_refused(self, db: sqlite3.Connection) -> None:
        with refuses(db):
            insert(db, "cases", a_case())
            insert(db, "action_intents", an_intent())
            insert(db, "approvals", an_approval(decided_by="system"))

    def test_a_matching_approval_is_accepted(self, db: sqlite3.Connection) -> None:
        """The positive control, or the trigger could be refusing everything."""
        self.setup_case(db)
        insert(
            db,
            "approvals",
            an_approval(subject="CREDIT", message_kind=None, amount_paise=182000),
        )
        insert(
            db,
            "actions",
            an_action(
                action_type="POST_CREDIT",
                approval_id="apr_01HQ",
                credit_shipment_ref="SHP-0001",
                credit_reason_code="calculation_error",
            ),
        )

    def test_one_approval_can_only_be_used_once(self, db: sqlite3.Connection) -> None:
        self.setup_case(db)
        insert(
            db,
            "approvals",
            an_approval(
                subject="CREDIT",
                message_kind=None,
                amount_paise=182000,
                used_by_action_id="act_01HQ",
            ),
        )
        with refuses(db, "UNIQUE"):
            insert(
                db,
                "approvals",
                an_approval(
                    id="apr_02HQ",
                    subject="CREDIT",
                    message_kind=None,
                    amount_paise=182000,
                    used_by_action_id="act_01HQ",
                ),
            )

    def test_only_one_live_credit_per_shipment_and_reason(self, db: sqlite3.Connection) -> None:
        """ux_actions_one_credit (I3): across all cases, not just this one."""
        self.setup_case(db)
        insert(
            db, "approvals", an_approval(subject="CREDIT", message_kind=None, amount_paise=182000)
        )
        insert(
            db,
            "actions",
            an_action(
                action_type="POST_CREDIT",
                approval_id="apr_01HQ",
                credit_shipment_ref="SHP-0001",
                credit_reason_code="calculation_error",
            ),
        )
        insert(
            db,
            "action_intents",
            an_intent(
                id="int_02HQ",
                action_type="POST_CREDIT",
                message_kind=None,
                amount_paise=182000,
                reason_code="calculation_error",
            ),
        )
        insert(
            db,
            "approvals",
            an_approval(
                id="apr_02HQ",
                intent_id="int_02HQ",
                subject="CREDIT",
                message_kind=None,
                amount_paise=182000,
            ),
        )
        with refuses(db, "UNIQUE"):
            insert(
                db,
                "actions",
                an_action(
                    id="act_02HQ",
                    intent_id="int_02HQ",
                    action_seq=2,
                    idempotency_key="wd1:" + "c" * 64,
                    action_type="POST_CREDIT",
                    approval_id="apr_02HQ",
                    credit_shipment_ref="SHP-0001",
                    credit_reason_code="calculation_error",
                ),
            )


class TestActionStatusMoves:
    """tr_actions_status (I4). VERIFIED and FAILED are final; UNKNOWN needs a human."""

    def setup_action(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "action_intents", an_intent())
        insert(db, "actions", an_action())

    def test_planned_to_sent_is_allowed(self, db: sqlite3.Connection) -> None:
        self.setup_action(db)
        db.execute(
            "UPDATE actions SET status='SENT', sent_at=?, external_ref='x' WHERE id='act_01HQ'",
            (TS,),
        )

    def test_sent_cannot_go_back_to_planned(self, db: sqlite3.Connection) -> None:
        self.setup_action(db)
        db.execute(
            "UPDATE actions SET status='SENT', sent_at=?, external_ref='x' WHERE id='act_01HQ'",
            (TS,),
        )
        with refuses(db, "illegal_action_status_change"):
            db.execute("UPDATE actions SET status='PLANNED' WHERE id='act_01HQ'")

    def test_unknown_cannot_become_verified_without_a_human(self, db: sqlite3.Connection) -> None:
        """I4: an unknown outcome is never quietly promoted to success."""
        self.setup_action(db)
        db.execute(
            "UPDATE actions SET status='SENT', sent_at=?, external_ref='x' WHERE id='act_01HQ'",
            (TS,),
        )
        db.execute("UPDATE actions SET status='UNKNOWN', unknown_at=? WHERE id='act_01HQ'", (TS,))
        with refuses(db, "illegal_action_status_change"):
            db.execute(
                "UPDATE actions SET status='VERIFIED', verified_at=?, verified_by='system' "
                "WHERE id='act_01HQ'",
                (TS,),
            )

    def test_unknown_becomes_verified_when_a_human_says_so(self, db: sqlite3.Connection) -> None:
        """Via SENT, because an action nobody sent has no external reference to verify
        against — the schema says so through the SENT/VERIFIED check, which is a sharper
        statement of I4 than the status trigger alone."""
        self.setup_action(db)
        db.execute(
            "UPDATE actions SET status='SENT', sent_at=?, external_ref='x' WHERE id='act_01HQ'",
            (TS,),
        )
        db.execute("UPDATE actions SET status='UNKNOWN', unknown_at=? WHERE id='act_01HQ'", (TS,))
        db.execute(
            "UPDATE actions SET status='VERIFIED', verified_at=?, verified_by='human:owner' "
            "WHERE id='act_01HQ'",
            (TS,),
        )

    def test_the_identity_of_an_action_cannot_change(self, db: sqlite3.Connection) -> None:
        """Changing the key or the hash after planning would make the read-back meaningless."""
        self.setup_action(db)
        with refuses(db, "action_identity_is_immutable"):
            db.execute(
                "UPDATE actions SET idempotency_key=? WHERE id='act_01HQ'", ("wd1:" + "d" * 64,)
            )

    def test_an_idempotency_key_cannot_repeat(self, db: sqlite3.Connection) -> None:
        self.setup_action(db)
        insert(db, "action_intents", an_intent(id="int_02HQ"))
        with refuses(db, "UNIQUE"):
            insert(db, "actions", an_action(id="act_02HQ", intent_id="int_02HQ", action_seq=2))


class TestTimers:
    def test_only_one_active_timer_per_case_and_kind(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "timers", a_timer())
        with refuses(db, "UNIQUE"):
            insert(db, "timers", a_timer(id="tmr_02HQ"))

    def test_a_cancelled_timer_frees_the_slot(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "timers", a_timer(cancelled_at=TS, cancel_reason="state_left"))
        insert(db, "timers", a_timer(id="tmr_02HQ"))

    def test_gather_round_belongs_to_evidence_retry_alone(self, db: sqlite3.Connection) -> None:
        """D41: the retry limit is counted per round, so the round has to be on the row."""
        insert(db, "cases", a_case())
        with refuses(db):
            insert(db, "timers", a_timer(gather_round=1))
        with refuses(db):
            insert(db, "timers", a_timer(id="tmr_02HQ", kind="EVIDENCE_RETRY"))
        insert(db, "timers", a_timer(id="tmr_03HQ", kind="EVIDENCE_RETRY", gather_round=1))

    def test_a_timer_cannot_both_fire_and_be_cancelled(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(db, "timers", a_timer(fired_at=TS, cancelled_at=TS, cancel_reason="x"))

    def test_a_timer_cannot_be_consumed_before_it_fires(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(db, "timers", a_timer(consumed_at=TS))

    def test_due_cannot_precede_the_anchor(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(db, "timers", a_timer(due_at=TS, anchor_at=LATER))


class TestApprovals:
    def test_an_approval_cannot_expire_before_it_was_requested(
        self, db: sqlite3.Connection
    ) -> None:
        insert(db, "cases", a_case())
        insert(db, "action_intents", an_intent())
        with refuses(db):
            insert(db, "approvals", an_approval(expires_at=TS, requested_at=LATER))

    def test_at_most_one_pending_approval_per_intent(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "action_intents", an_intent())
        insert(db, "approvals", an_approval(decision="PENDING", decided_by=None, decided_at=None))
        with refuses(db, "UNIQUE"):
            insert(
                db,
                "approvals",
                an_approval(id="apr_02HQ", decision="PENDING", decided_by=None, decided_at=None),
            )

    def test_a_used_approval_must_have_been_approved(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "action_intents", an_intent())
        with refuses(db):
            insert(
                db,
                "approvals",
                an_approval(decision="REJECTED", used_by_action_id="act_01HQ"),
            )


class TestOutcomeOutbox:
    def test_resolved_is_only_for_the_two_resolved_states(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        with refuses(db):
            insert(
                db,
                "outcome_outbox",
                {
                    "id": "out_01HQ",
                    "case_id": "case_01HQ",
                    "state": "WITHDRAWN",
                    "resolved": 1,
                    "outcome_json": "{}",
                    "created_at": TS,
                },
            )


class TestIdFormats:
    @pytest.mark.parametrize(
        ("table", "row"),
        [
            ("cases", a_case(id="wrong_01HQ")),
            ("events", an_event(id="wrong_01HQ")),
        ],
    )
    def test_an_id_must_carry_its_prefix(
        self, db: sqlite3.Connection, table: str, row: dict[str, object]
    ) -> None:
        if table != "cases":
            insert(db, "cases", a_case())
        with refuses(db):
            insert(db, table, row)

    def test_an_idempotency_key_must_be_the_right_shape(self, db: sqlite3.Connection) -> None:
        insert(db, "cases", a_case())
        insert(db, "action_intents", an_intent())
        with refuses(db):
            insert(db, "actions", an_action(idempotency_key="not-a-key"))

    def test_a_timestamp_must_carry_microseconds_and_a_z(self, db: sqlite3.Connection) -> None:
        """Lexical order is time order only if every timestamp has the same shape."""
        with refuses(db):
            insert(db, "cases", a_case(opened_at="2026-03-01T09:30:00Z"))


def test_the_reference_ddl_builds_every_object(db: sqlite3.Connection) -> None:
    """A guard on the fixture itself: if the schema stopped loading, every test above would
    fail for the same confusing reason."""
    counts = dict(db.execute("SELECT type, count(*) FROM sqlite_master GROUP BY type").fetchall())
    assert counts["table"] == 16
    assert counts["trigger"] == 9
    assert counts["index"] >= 40
    assert KEY and HASH  # the fixtures are importable
