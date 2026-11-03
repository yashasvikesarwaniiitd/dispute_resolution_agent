"""SQLAlchemy Core tables (schema.sql).

These describe the schema for querying. They do **not** create it: the migration executes
`schema.sql` verbatim, because the parts that carry the invariants — partial unique indexes,
CHECK constraints spanning columns, and eight triggers — are either awkward or impossible to
express in Core, and a hand-transcription of 534 lines of DDL is 534 chances to weaken one by
accident. `tests/unit/infra/test_schema_conformance.py` compares these definitions against
the database the migration actually built, so the two cannot drift.

Money is INTEGER paise and weight INTEGER grams (ADR 0011). Timestamps are ISO 8601 UTC text
with microseconds and a "Z", so lexical order is time order and a BETWEEN on text works.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    UniqueConstraint,
)

metadata = MetaData()

legal_transitions = Table(
    "legal_transitions",
    metadata,
    Column("from_state", Text, primary_key=True),
    Column("to_state", Text, primary_key=True),
)

cases = Table(
    "cases",
    metadata,
    Column("id", Text, primary_key=True),
    Column("pack", Text, nullable=False),
    Column("shipment_ref", Text, nullable=False),
    Column("claim_ref", Text, nullable=False),
    Column("seller_ref", Text, nullable=False),
    Column("seller_contact", Text, nullable=False),
    Column("facts_json", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("version", Integer, nullable=False),
    Column("correlation_id", Text, nullable=False),
    Column("opened_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("state_entered_at", Text, nullable=False),
    Column("closed_at", Text),
    Column("review_code", Text),
    Column("review_module", Text),
    Column("review_detail", Text),
    Column("pending_intent_id", Text),
    Column("action_seq", Integer, nullable=False, server_default="0"),
    Column("reopen_count", Integer, nullable=False, server_default="0"),
    Column("rejection_count", Integer, nullable=False, server_default="0"),
    Column("gather_round", Integer, nullable=False, server_default="0"),
    Column("prior_case_id", Text),
    Column("superseded_by_case_id", Text),
)

#: The states a case is "open" in. The partial unique index below uses exactly this list, and
#: it is what makes "one open case per shipment and claim" a database guarantee rather than a
#: check with a race in it.
OPEN_STATES: tuple[str, ...] = (
    "RECEIVED",
    "GATHERING_EVIDENCE",
    "CALCULATING",
    "INVESTIGATING",
    "NEEDS_HUMAN_REVIEW",
    "AWAITING_SELLER",
    "AWAITING_OPS",
    "AWAITING_APPROVAL",
    "EXECUTING",
    "VERIFYING",
)

inbox = Table(
    "inbox",
    metadata,
    Column("id", Text, primary_key=True),
    Column("dedupe_key", Text, nullable=False, unique=True),
    Column("source", Text, nullable=False),
    Column("source_event_id", Text, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("event_json", Text, nullable=False),
    Column("received_at", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("case_id", Text),
    Column("event_id", Text),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
    Column("next_attempt_at", Text),
)

events = Table(
    "events",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("type", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("occurred_at", Text, nullable=False),
    Column("recorded_at", Text, nullable=False),
    Column("actor_kind", Text, nullable=False),
    Column("actor_id", Text, nullable=False),
    Column("cause_kind", Text, nullable=False),
    Column("cause_ref", Text, nullable=False),
    Column("correlation_id", Text, nullable=False),
    Column("causation_id", Text),
    Column("dedupe_key", Text, unique=True),
    Column("from_state", Text),
    Column("to_state", Text),
    Column("payload_json", Text, nullable=False),
    Column("schema_version", Integer, nullable=False, server_default="1"),
    UniqueConstraint("case_id", "seq"),
)

evidence_items = Table(
    "evidence_items",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("gather_round", Integer, nullable=False),
    Column("requirement_key", Text, nullable=False),
    Column("source", Text, nullable=False),
    Column("provenance", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("value_json", Text),
    Column("untrusted_json", Text, nullable=False, server_default="[]"),
    Column("error_code", Text),
    Column("retrieved_at", Text, nullable=False),
    Column("content_hash", Text, nullable=False),
    UniqueConstraint("case_id", "gather_round", "requirement_key", "source"),
)

assessments = Table(
    "assessments",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("gather_round", Integer, nullable=False),
    Column("pack", Text, nullable=False),
    Column("pack_version", Text, nullable=False),
    Column("eligible", Text, nullable=False),
    Column("charged_paise", Integer),
    Column("recalculated_paise", Integer),
    Column("discrepancy_paise", Integer),
    Column("result_json", Text, nullable=False),
    Column("input_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    UniqueConstraint("case_id", "gather_round"),
)

proposals = Table(
    "proposals",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("gather_round", Integer, nullable=False),
    Column("action", Text, nullable=False),
    Column("confidence", Text, nullable=False),
    Column("reasoner", Text, nullable=False),
    Column("fallback_used", Integer, nullable=False),
    Column("proposal_json", Text, nullable=False),
    Column("prompt_name", Text),
    Column("prompt_version", Text),
    Column("prompt_hash", Text),
    Column("model_id", Text),
    Column("model_route", Text),
    Column("briefing_hash", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

action_intents = Table(
    "action_intents",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("source_kind", Text, nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("action_type", Text, nullable=False),
    Column("message_kind", Text),
    Column("payload_json", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("amount_paise", Integer),
    Column("reason_code", Text),
    Column("assessment_id", Text),
    Column("status", Text, nullable=False),
    Column("created_at", Text, nullable=False),
)

policy_verdicts = Table(
    "policy_verdicts",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("intent_id", Text),
    Column("stage", Text, nullable=False),
    Column("verdict", Text, nullable=False),
    Column("results_json", Text, nullable=False),
    Column("signals_json", Text, nullable=False),
    Column("review_reason", Text),
    Column("rules_version", Text, nullable=False),
    Column("evaluated_at", Text, nullable=False),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("intent_id", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("payload_hash", Text, nullable=False),
    Column("amount_paise", Integer),
    Column("message_kind", Text),
    Column("requested_at", Text, nullable=False),
    Column("expires_at", Text, nullable=False),
    Column("notified_at", Text),
    Column("decision", Text, nullable=False),
    Column("decided_by", Text),
    Column("decided_at", Text),
    Column("note", Text, nullable=False, server_default=""),
    Column("used_by_action_id", Text, unique=True),
)

actions = Table(
    "actions",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("intent_id", Text, nullable=False, unique=True),
    Column("approval_id", Text, unique=True),
    Column("action_type", Text, nullable=False),
    Column("action_seq", Integer, nullable=False),
    Column("idempotency_key", Text, nullable=False, unique=True),
    Column("payload_hash", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("external_ref", Text),
    Column("last_error_code", Text),
    Column("last_error_detail", Text),
    Column("planned_at", Text, nullable=False),
    Column("sent_at", Text),
    Column("verified_at", Text),
    Column("failed_at", Text),
    Column("unknown_at", Text),
    Column("verify_attempts", Integer, nullable=False, server_default="0"),
    Column("verify_deadline_at", Text),
    Column("verified_by", Text),
    Column("verification_json", Text),
    Column("claimed_by", Text),
    Column("claim_expires_at", Text),
    Column("credit_shipment_ref", Text),
    Column("credit_reason_code", Text),
    UniqueConstraint("case_id", "action_seq"),
    CheckConstraint(
        "action_type <> 'POST_CREDIT' OR approval_id IS NOT NULL",
        name="ck_actions_credit_needs_approval",
    ),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("direction", Text, nullable=False),
    Column("action_id", Text),
    Column("inbox_id", Text),
    Column("channel", Text, nullable=False),
    Column("external_message_id", Text, nullable=False),
    Column("internet_message_id", Text),
    Column("conversation_id", Text),
    Column("from_addr", Text, nullable=False),
    Column("to_addr", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("body_hash", Text, nullable=False),
    Column("kind", Text),
    Column("at", Text, nullable=False),
    Column("interpreted_round", Integer),
    UniqueConstraint("channel", "direction", "external_message_id"),
)

timers = Table(
    "timers",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("kind", Text, nullable=False),
    Column("due_at", Text, nullable=False),
    Column("anchor_at", Text, nullable=False),
    Column("armed_in_state", Text, nullable=False),
    Column("armed_at_version", Integer, nullable=False),
    Column("gather_round", Integer),
    Column("correlation_id", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("fired_at", Text),
    Column("consumed_at", Text),
    Column("voided_at", Text),
    Column("cancelled_at", Text),
    Column("cancel_reason", Text),
)

outcome_outbox = Table(
    "outcome_outbox",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("resolved", Integer, nullable=False),
    Column("outcome_json", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("delivered_at", Text),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("last_error", Text),
)

control_flags = Table(
    "control_flags",
    metadata,
    Column("name", Text, primary_key=True),
    Column("value", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
    Column("updated_by", Text, nullable=False),
)

model_calls = Table(
    "model_calls",
    metadata,
    Column("id", Text, primary_key=True),
    Column("case_id", Text),
    Column("purpose", Text, nullable=False),
    Column("route", Text, nullable=False),
    Column("provider", Text, nullable=False),
    Column("deployment", Text),
    Column("model_id", Text, nullable=False),
    Column("prompt_name", Text, nullable=False),
    Column("prompt_version", Text, nullable=False),
    Column("prompt_hash", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("latency_ms", Integer, nullable=False),
    Column("cost_usd_micros", Integer, nullable=False),
    Column("status", Text, nullable=False),
    Column("error_code", Text),
    Column("failover_from", Text),
    Column("created_at", Text, nullable=False),
)

Index("ix_cases_state", cases.c.state, cases.c.updated_at)
Index("ix_events_correlation", events.c.correlation_id)

#: Every table schema.sql declares, for the conformance test.
ALL_TABLES: tuple[str, ...] = tuple(sorted(metadata.tables))
