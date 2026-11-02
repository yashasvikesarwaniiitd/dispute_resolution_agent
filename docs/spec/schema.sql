-- Kernel schema (Stage A1). SQLite 3.37+ (STRICT tables). Phase 1 writes this as the first Alembic
-- migration using SQLAlchemy Core; this file is the reference DDL and is loaded verbatim by a
-- schema-conformance test (sqlite3, in-memory) to prove it is valid.
--
-- SQLite specifics (applied by infra/sqlite on every connection, verified at startup):
--   PRAGMA foreign_keys = ON;      -- off by default in SQLite; without it FKs are decoration
--   PRAGMA journal_mode = WAL;     -- readers do not block the single writer
--   PRAGMA synchronous = FULL;     -- money and audit rows survive power loss
--   PRAGMA busy_timeout = 5000;    -- wait up to 5 s for the write lock instead of failing
--   Every write transaction is BEGIN IMMEDIATE (takes the write lock up front, so two handlers
--   never both read and then fail at commit). Startup refuses sqlite_version < 3.37.0.
--
-- Conventions:
--   Ids: TEXT "<prefix>_<ULID>". Timestamps: TEXT ISO 8601 UTC with microseconds and "Z"
--   (e.g. 2026-11-02T09:15:00.000000Z); lexical order = time order. Money: INTEGER paise.
--   Weight: INTEGER grams. JSON: TEXT, checked with json_valid(). Hashes: "sha256:<64 hex>".
--   Booleans: INTEGER 0/1 with CHECK.
--   The one-open-case rule, the idempotency key, the event dedupe key, the approval single-use
--   rule and "no credit without an approval" are all enforced here, not only in Python.

-- ---------------------------------------------------------------------------------------------
-- Legal state pairs, generated from docs/spec/transitions.yaml. A trigger on cases refuses any
-- state change not listed (defence in depth for I8: a write that bypassed the state machine fails).
CREATE TABLE legal_transitions (
  from_state TEXT NOT NULL,
  to_state   TEXT NOT NULL,
  PRIMARY KEY (from_state, to_state)
) STRICT, WITHOUT ROWID;

INSERT INTO legal_transitions (from_state, to_state) VALUES
  ('AWAITING_APPROVAL', 'AWAITING_SELLER'),
  ('AWAITING_APPROVAL', 'CLOSED_NO_RESPONSE'),
  ('AWAITING_APPROVAL', 'EXECUTING'),
  ('AWAITING_APPROVAL', 'GATHERING_EVIDENCE'),
  ('AWAITING_APPROVAL', 'INVESTIGATING'),
  ('AWAITING_APPROVAL', 'NEEDS_HUMAN_REVIEW'),
  ('AWAITING_OPS', 'GATHERING_EVIDENCE'),
  ('AWAITING_OPS', 'NEEDS_HUMAN_REVIEW'),
  ('AWAITING_OPS', 'WITHDRAWN'),
  ('AWAITING_SELLER', 'AWAITING_APPROVAL'),
  ('AWAITING_SELLER', 'CLOSED_NO_RESPONSE'),
  ('AWAITING_SELLER', 'EXECUTING'),
  ('AWAITING_SELLER', 'GATHERING_EVIDENCE'),
  ('AWAITING_SELLER', 'NEEDS_HUMAN_REVIEW'),
  ('AWAITING_SELLER', 'WITHDRAWN'),
  ('CALCULATING', 'INVESTIGATING'),
  ('CALCULATING', 'NEEDS_HUMAN_REVIEW'),
  ('CALCULATING', 'REJECTED_INELIGIBLE'),
  ('CLOSED_NO_RESPONSE', 'GATHERING_EVIDENCE'),
  ('CLOSED_NO_RESPONSE', 'NEEDS_HUMAN_REVIEW'),
  ('EXECUTING', 'NEEDS_HUMAN_REVIEW'),
  ('EXECUTING', 'VERIFYING'),
  ('GATHERING_EVIDENCE', 'CALCULATING'),
  ('GATHERING_EVIDENCE', 'NEEDS_HUMAN_REVIEW'),
  ('INVESTIGATING', 'AWAITING_APPROVAL'),
  ('INVESTIGATING', 'AWAITING_OPS'),
  ('INVESTIGATING', 'AWAITING_SELLER'),
  ('INVESTIGATING', 'EXECUTING'),
  ('INVESTIGATING', 'NEEDS_HUMAN_REVIEW'),
  ('NEEDS_HUMAN_REVIEW', 'AWAITING_APPROVAL'),
  ('NEEDS_HUMAN_REVIEW', 'EXECUTING'),
  ('NEEDS_HUMAN_REVIEW', 'GATHERING_EVIDENCE'),
  ('NEEDS_HUMAN_REVIEW', 'HANDED_TO_HUMAN'),
  ('NEEDS_HUMAN_REVIEW', 'WITHDRAWN'),
  ('RECEIVED', 'GATHERING_EVIDENCE'),
  ('RECEIVED', 'NEEDS_HUMAN_REVIEW'),
  ('RECEIVED', 'REJECTED_INELIGIBLE'),
  ('REJECTED_INELIGIBLE', 'GATHERING_EVIDENCE'),
  ('RESOLVED_CREDITED', 'GATHERING_EVIDENCE'),
  ('RESOLVED_UPHELD', 'GATHERING_EVIDENCE'),
  ('RESOLVED_UPHELD', 'NEEDS_HUMAN_REVIEW'),
  ('VERIFYING', 'AWAITING_APPROVAL'),
  ('VERIFYING', 'AWAITING_OPS'),
  ('VERIFYING', 'AWAITING_SELLER'),
  ('VERIFYING', 'EXECUTING'),
  ('VERIFYING', 'HANDED_TO_HUMAN'),
  ('VERIFYING', 'NEEDS_HUMAN_REVIEW'),
  ('VERIFYING', 'RESOLVED_CREDITED'),
  ('VERIFYING', 'RESOLVED_UPHELD'),
  ('WITHDRAWN', 'GATHERING_EVIDENCE');

-- ---------------------------------------------------------------------------------------------
CREATE TABLE cases (
  id                     TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'case_*'),
  pack                   TEXT    NOT NULL,
  shipment_ref           TEXT    NOT NULL CHECK (length(shipment_ref) BETWEEN 1 AND 64),
  claim_ref              TEXT    NOT NULL CHECK (length(claim_ref) BETWEEN 1 AND 64),
  seller_ref             TEXT    NOT NULL CHECK (length(seller_ref) BETWEEN 1 AND 64),
  seller_contact         TEXT    NOT NULL,
  facts_json             TEXT    NOT NULL CHECK (json_valid(facts_json)),
  state                  TEXT    NOT NULL CHECK (state IN (
                           'RECEIVED','GATHERING_EVIDENCE','CALCULATING','INVESTIGATING',
                           'NEEDS_HUMAN_REVIEW','AWAITING_SELLER','AWAITING_OPS','AWAITING_APPROVAL',
                           'EXECUTING','VERIFYING','RESOLVED_CREDITED','RESOLVED_UPHELD','WITHDRAWN',
                           'CLOSED_NO_RESPONSE','REJECTED_INELIGIBLE','HANDED_TO_HUMAN')),
  version                INTEGER NOT NULL CHECK (version >= 1),
  correlation_id         TEXT    NOT NULL CHECK (correlation_id GLOB 'corr_*'),
  opened_at              TEXT    NOT NULL CHECK (opened_at GLOB '????-??-??T??:??:??.??????Z'),
  updated_at             TEXT    NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.??????Z'),
  state_entered_at       TEXT    NOT NULL CHECK (state_entered_at GLOB '????-??-??T??:??:??.??????Z'),
  closed_at              TEXT             CHECK (closed_at GLOB '????-??-??T??:??:??.??????Z'),
  review_code            TEXT,
  review_module          TEXT,
  review_detail          TEXT,
  pending_intent_id      TEXT,             -- FK added logically; see note on circular references below
  action_seq             INTEGER NOT NULL DEFAULT 0 CHECK (action_seq >= 0),
  reopen_count           INTEGER NOT NULL DEFAULT 0 CHECK (reopen_count >= 0),
  rejection_count        INTEGER NOT NULL DEFAULT 0 CHECK (rejection_count >= 0),
  gather_round           INTEGER NOT NULL DEFAULT 0 CHECK (gather_round >= 0),
  prior_case_id          TEXT    REFERENCES cases(id),
  superseded_by_case_id  TEXT    REFERENCES cases(id),
  -- closed_at is set exactly when the state is terminal
  CHECK ((closed_at IS NOT NULL) = (state IN ('RESOLVED_CREDITED','RESOLVED_UPHELD','WITHDRAWN',
                                              'CLOSED_NO_RESPONSE','REJECTED_INELIGIBLE','HANDED_TO_HUMAN'))),
  -- a review reason exists exactly when the case is in NEEDS_HUMAN_REVIEW
  CHECK ((review_code IS NOT NULL) = (state = 'NEEDS_HUMAN_REVIEW')),
  CHECK (review_code IS NOT 'module_unavailable' OR review_module IS NOT NULL),
  CHECK (updated_at >= opened_at)
) STRICT;

-- One open case per shipment and claim. "Open" = any working state.
CREATE UNIQUE INDEX ux_cases_open_claim ON cases (shipment_ref, claim_ref)
  WHERE state IN ('RECEIVED','GATHERING_EVIDENCE','CALCULATING','INVESTIGATING','NEEDS_HUMAN_REVIEW',
                  'AWAITING_SELLER','AWAITING_OPS','AWAITING_APPROVAL','EXECUTING','VERIFYING');
CREATE INDEX ix_cases_claim_opened ON cases (shipment_ref, claim_ref, opened_at);
CREATE INDEX ix_cases_seller ON cases (seller_ref, opened_at);
CREATE INDEX ix_cases_state ON cases (state, updated_at);

-- Optimistic concurrency: every update bumps version by exactly 1.
CREATE TRIGGER tr_cases_version BEFORE UPDATE ON cases
WHEN NEW.version <> OLD.version + 1
BEGIN SELECT RAISE(ABORT, 'version_must_increment_by_one'); END;

-- Only legal state changes (I8).
CREATE TRIGGER tr_cases_legal_transition BEFORE UPDATE OF state ON cases
WHEN NEW.state <> OLD.state AND NOT EXISTS (
  SELECT 1 FROM legal_transitions WHERE from_state = OLD.state AND to_state = NEW.state)
BEGIN SELECT RAISE(ABORT, 'illegal_transition'); END;

-- A new case always starts in RECEIVED at version 1.
CREATE TRIGGER tr_cases_insert BEFORE INSERT ON cases
WHEN NEW.state <> 'RECEIVED' OR NEW.version <> 1
BEGIN SELECT RAISE(ABORT, 'case_must_start_received_v1'); END;

CREATE TRIGGER tr_cases_no_delete BEFORE DELETE ON cases
BEGIN SELECT RAISE(ABORT, 'cases_are_never_deleted'); END;

-- ---------------------------------------------------------------------------------------------
-- Inbox: every inbound event lands here first (dedupe), then is dispatched. Mutable status; it is
-- not the audit trail.
CREATE TABLE inbox (
  id               TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'inb_*'),
  dedupe_key       TEXT    NOT NULL UNIQUE CHECK (length(dedupe_key) BETWEEN 3 AND 300),
  source           TEXT    NOT NULL,
  source_event_id  TEXT    NOT NULL,
  event_type       TEXT    NOT NULL CHECK (event_type IN (
                     'DISPUTE_RAISED','SELLER_REPLY_RECEIVED','DISPUTE_WITHDRAWN','DELIVERY_FAILURE_RECEIVED',
                     'OPS_FINDING_RECEIVED','APPROVAL_DECIDED','TIMER_FIRED','HUMAN_TOOK_CASE',
                     'HUMAN_RETURNED_CASE','HUMAN_REOPENED_CASE','HUMAN_CONFIRMED_WITHDRAWAL')),
  event_json       TEXT    NOT NULL CHECK (json_valid(event_json)),   -- the full InboundEvent
  received_at      TEXT    NOT NULL CHECK (received_at GLOB '????-??-??T??:??:??.??????Z'),
  status           TEXT    NOT NULL CHECK (status IN ('PENDING','PROCESSED','DEFERRED','UNROUTABLE','FAILED')),
  case_id          TEXT    REFERENCES cases(id),
  event_id         TEXT    REFERENCES events(id),
  attempts         INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error       TEXT,
  next_attempt_at  TEXT    CHECK (next_attempt_at GLOB '????-??-??T??:??:??.??????Z'),
  CHECK (dedupe_key = source || ':' || source_event_id),
  CHECK (status NOT IN ('PROCESSED','DEFERRED') OR (case_id IS NOT NULL AND event_id IS NOT NULL))
) STRICT;
CREATE INDEX ix_inbox_status ON inbox (status, received_at);
CREATE INDEX ix_inbox_case_deferred ON inbox (case_id, received_at) WHERE status = 'DEFERRED';

-- ---------------------------------------------------------------------------------------------
-- Events: the append-only audit trail (I8). One row per input and per internal change.
CREATE TABLE events (
  id              TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'evt_*'),
  case_id         TEXT    NOT NULL REFERENCES cases(id),
  seq             INTEGER NOT NULL CHECK (seq >= 1),
  type            TEXT    NOT NULL,
  category        TEXT    NOT NULL CHECK (category IN ('INPUT','INTERNAL')),
  occurred_at     TEXT    NOT NULL CHECK (occurred_at GLOB '????-??-??T??:??:??.??????Z'),
  recorded_at     TEXT    NOT NULL CHECK (recorded_at GLOB '????-??-??T??:??:??.??????Z'),
  actor_kind      TEXT    NOT NULL CHECK (actor_kind IN ('SYSTEM','HUMAN','EXTERNAL')),
  actor_id        TEXT    NOT NULL,
  cause_kind      TEXT    NOT NULL CHECK (cause_kind IN ('INPUT_EVENT','STEP','TIMER','HUMAN','RECOVERY')),
  cause_ref       TEXT    NOT NULL,
  correlation_id  TEXT    NOT NULL CHECK (correlation_id GLOB 'corr_*'),
  causation_id    TEXT    REFERENCES events(id),
  dedupe_key      TEXT    UNIQUE,
  from_state      TEXT,   -- STATE_CHANGED only (denormalised for timeline queries)
  to_state        TEXT,
  payload_json    TEXT    NOT NULL CHECK (json_valid(payload_json)),
  schema_version  INTEGER NOT NULL DEFAULT 1,
  UNIQUE (case_id, seq),
  CHECK ((category = 'INPUT') = (dedupe_key IS NOT NULL)),
  CHECK ((category = 'INPUT') = (causation_id IS NULL)),
  CHECK ((type = 'STATE_CHANGED') = (to_state IS NOT NULL))
) STRICT;
CREATE INDEX ix_events_correlation ON events (correlation_id);
CREATE INDEX ix_events_type_time ON events (type, recorded_at);

CREATE TRIGGER tr_events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;
CREATE TRIGGER tr_events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;

-- ---------------------------------------------------------------------------------------------
CREATE TABLE evidence_items (
  id               TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'evi_*'),
  case_id          TEXT    NOT NULL REFERENCES cases(id),
  gather_round     INTEGER NOT NULL CHECK (gather_round >= 1),
  requirement_key  TEXT    NOT NULL,
  source           TEXT    NOT NULL,
  provenance       TEXT    NOT NULL CHECK (provenance IN ('SYSTEM','SELLER','OPS','AI_DERIVED')),
  status           TEXT    NOT NULL CHECK (status IN ('FOUND','MISSING','FAILED')),
  value_json       TEXT    CHECK (value_json IS NULL OR json_valid(value_json)),
  untrusted_json   TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(untrusted_json)),
  error_code       TEXT,
  retrieved_at     TEXT    NOT NULL CHECK (retrieved_at GLOB '????-??-??T??:??:??.??????Z'),
  content_hash     TEXT    NOT NULL CHECK (content_hash GLOB 'sha256:*'),
  -- never invented: a value exists only when FOUND; an error code only when FAILED
  CHECK ((status = 'FOUND') = (value_json IS NOT NULL)),
  CHECK ((status = 'FAILED') = (error_code IS NOT NULL)),
  UNIQUE (case_id, gather_round, requirement_key, source)
) STRICT;

CREATE TABLE assessments (
  id            TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'asm_*'),
  case_id       TEXT    NOT NULL REFERENCES cases(id),
  gather_round  INTEGER NOT NULL CHECK (gather_round >= 1),
  pack          TEXT    NOT NULL,
  pack_version  TEXT    NOT NULL,
  eligible      TEXT    NOT NULL CHECK (eligible IN ('eligible','ineligible','unknown')),
  charged_paise       INTEGER CHECK (charged_paise >= 0),
  recalculated_paise  INTEGER CHECK (recalculated_paise >= 0),
  discrepancy_paise   INTEGER CHECK (discrepancy_paise >= 0),
  result_json   TEXT    NOT NULL CHECK (json_valid(result_json)),   -- full ChargeAssessment incl. facts
  input_hash    TEXT    NOT NULL CHECK (input_hash GLOB 'sha256:*'),
  created_at    TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z'),
  UNIQUE (case_id, gather_round)
) STRICT;

CREATE TABLE proposals (
  id              TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'prp_*'),
  case_id         TEXT    NOT NULL REFERENCES cases(id),
  gather_round    INTEGER NOT NULL CHECK (gather_round >= 1),
  action          TEXT    NOT NULL CHECK (action IN ('propose_credit','uphold_charge','request_seller_evidence',
                                                     'request_remeasurement','escalate_to_human','wait')),
  confidence      TEXT    NOT NULL CHECK (confidence IN ('low','medium','high')),
  reasoner        TEXT    NOT NULL CHECK (reasoner IN ('rules_only','llm')),
  fallback_used   INTEGER NOT NULL CHECK (fallback_used IN (0,1)),
  proposal_json   TEXT    NOT NULL CHECK (json_valid(proposal_json)),
  prompt_name     TEXT,
  prompt_version  TEXT,
  prompt_hash     TEXT,
  model_id        TEXT,
  model_route     TEXT,
  briefing_hash   TEXT    NOT NULL CHECK (briefing_hash GLOB 'sha256:*'),
  created_at      TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z'),
  -- an LLM proposal always records which prompt produced it (CLAUDE.md section 9)
  CHECK (reasoner <> 'llm' OR (prompt_name IS NOT NULL AND prompt_version IS NOT NULL
                               AND prompt_hash IS NOT NULL AND model_id IS NOT NULL))
) STRICT;
CREATE INDEX ix_proposals_case ON proposals (case_id, created_at);

-- ---------------------------------------------------------------------------------------------
-- Intents: the exact payload we want to act on, created before approval.
CREATE TABLE action_intents (
  id             TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'int_*'),
  case_id        TEXT    NOT NULL REFERENCES cases(id),
  source_kind    TEXT    NOT NULL CHECK (source_kind IN ('PROPOSAL','TIMER','FOLLOW_UP')),
  source_ref     TEXT    NOT NULL,
  action_type    TEXT    NOT NULL CHECK (action_type IN ('SEND_MESSAGE','UPSERT_ISSUE','POST_CREDIT')),
  message_kind   TEXT    CHECK (message_kind IN ('evidence_request','reminder','handed_to_human_notice',
                                                 'upheld_explanation','credit_confirmation')),
  payload_json   TEXT    NOT NULL CHECK (json_valid(payload_json)),
  payload_hash   TEXT    NOT NULL CHECK (payload_hash GLOB 'sha256:*'),
  amount_paise   INTEGER CHECK (amount_paise > 0),
  reason_code    TEXT,
  assessment_id  TEXT    REFERENCES assessments(id),
  status         TEXT    NOT NULL CHECK (status IN ('PENDING_APPROVAL','APPROVED','PLANNED','SUPERSEDED','REJECTED','EXPIRED')),
  created_at     TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z'),
  CHECK ((action_type = 'SEND_MESSAGE') = (message_kind IS NOT NULL)),
  CHECK ((action_type = 'POST_CREDIT') = (amount_paise IS NOT NULL AND reason_code IS NOT NULL))
) STRICT;
CREATE INDEX ix_intents_case ON action_intents (case_id, created_at);

CREATE TABLE policy_verdicts (
  id             TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'vrd_*'),
  case_id        TEXT    NOT NULL REFERENCES cases(id),
  intent_id      TEXT    REFERENCES action_intents(id),
  stage          TEXT    NOT NULL CHECK (stage IN ('PLANNING','EXECUTION')),
  verdict        TEXT    NOT NULL CHECK (verdict IN ('ALLOW','REQUIRE_APPROVAL','ESCALATE','DENY')),
  results_json   TEXT    NOT NULL CHECK (json_valid(results_json)),
  signals_json   TEXT    NOT NULL CHECK (json_valid(signals_json)),
  review_reason  TEXT,
  rules_version  TEXT    NOT NULL,
  evaluated_at   TEXT    NOT NULL CHECK (evaluated_at GLOB '????-??-??T??:??:??.??????Z'),
  CHECK ((verdict IN ('ESCALATE','DENY')) = (review_reason IS NOT NULL))
) STRICT;
CREATE INDEX ix_verdicts_intent ON policy_verdicts (intent_id);

-- ---------------------------------------------------------------------------------------------
-- Approvals: the stored approval record (I3, I13). A2 (approvals.md) may add columns by migration.
CREATE TABLE approvals (
  id                 TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'apr_*'),
  case_id            TEXT    NOT NULL REFERENCES cases(id),
  intent_id          TEXT    NOT NULL REFERENCES action_intents(id),
  subject            TEXT    NOT NULL CHECK (subject IN ('CREDIT','MESSAGE')),
  payload_hash       TEXT    NOT NULL CHECK (payload_hash GLOB 'sha256:*'),
  amount_paise       INTEGER CHECK (amount_paise > 0),
  message_kind       TEXT,
  requested_at       TEXT    NOT NULL CHECK (requested_at GLOB '????-??-??T??:??:??.??????Z'),
  expires_at         TEXT    NOT NULL CHECK (expires_at GLOB '????-??-??T??:??:??.??????Z'),
  notified_at        TEXT    CHECK (notified_at GLOB '????-??-??T??:??:??.??????Z'),
  decision           TEXT    NOT NULL CHECK (decision IN ('PENDING','APPROVED','REJECTED','EXPIRED','SUPERSEDED')),
  decided_by         TEXT,
  decided_at         TEXT    CHECK (decided_at GLOB '????-??-??T??:??:??.??????Z'),
  note               TEXT    NOT NULL DEFAULT '',
  used_by_action_id  TEXT    UNIQUE,     -- single use; FK to actions(id) enforced by the repository (circular)
  CHECK ((subject = 'CREDIT') = (amount_paise IS NOT NULL)),
  CHECK ((subject = 'MESSAGE') = (message_kind IS NOT NULL)),
  -- only a human approves or rejects
  CHECK (decision NOT IN ('APPROVED','REJECTED') OR (decided_by GLOB 'human:*' AND decided_at IS NOT NULL)),
  CHECK (used_by_action_id IS NULL OR decision = 'APPROVED'),
  CHECK (expires_at > requested_at)
) STRICT;
-- at most one live approval per intent
CREATE UNIQUE INDEX ux_approvals_pending_intent ON approvals (intent_id) WHERE decision = 'PENDING';
CREATE INDEX ix_approvals_queue ON approvals (decision, requested_at);

-- ---------------------------------------------------------------------------------------------
-- Actions: outbox-style execution records (I2). One row per executed intent.
CREATE TABLE actions (
  id                  TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'act_*'),
  case_id             TEXT    NOT NULL REFERENCES cases(id),
  intent_id           TEXT    NOT NULL UNIQUE REFERENCES action_intents(id),
  approval_id         TEXT    UNIQUE REFERENCES approvals(id),
  action_type         TEXT    NOT NULL CHECK (action_type IN ('SEND_MESSAGE','UPSERT_ISSUE','POST_CREDIT')),
  action_seq          INTEGER NOT NULL CHECK (action_seq >= 1),
  idempotency_key     TEXT    NOT NULL UNIQUE CHECK (idempotency_key GLOB 'wd1:*' AND length(idempotency_key) = 68),
  payload_hash        TEXT    NOT NULL CHECK (payload_hash GLOB 'sha256:*'),
  status              TEXT    NOT NULL CHECK (status IN ('PLANNED','SENT','VERIFIED','FAILED','UNKNOWN')),
  attempts            INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  external_ref        TEXT,
  last_error_code     TEXT,
  last_error_detail   TEXT,
  planned_at          TEXT    NOT NULL CHECK (planned_at GLOB '????-??-??T??:??:??.??????Z'),
  sent_at             TEXT    CHECK (sent_at GLOB '????-??-??T??:??:??.??????Z'),
  verified_at         TEXT    CHECK (verified_at GLOB '????-??-??T??:??:??.??????Z'),
  failed_at           TEXT    CHECK (failed_at GLOB '????-??-??T??:??:??.??????Z'),
  unknown_at          TEXT    CHECK (unknown_at GLOB '????-??-??T??:??:??.??????Z'),
  verify_attempts     INTEGER NOT NULL DEFAULT 0 CHECK (verify_attempts >= 0),
  verify_deadline_at  TEXT    CHECK (verify_deadline_at GLOB '????-??-??T??:??:??.??????Z'),
  verified_by         TEXT,   -- 'system' or 'human:<id>'
  verification_json   TEXT    CHECK (verification_json IS NULL OR json_valid(verification_json)),
  -- execution claim (lease): taken by a conditional UPDATE before any external write, so two
  -- workers can never execute the same action at once (executor.md section 3)
  claimed_by          TEXT,
  claim_expires_at    TEXT    CHECK (claim_expires_at GLOB '????-??-??T??:??:??.??????Z'),
  -- copied from the credit intent so the database can refuse a second live credit (I3)
  credit_shipment_ref TEXT,
  credit_reason_code  TEXT,
  CHECK ((claimed_by IS NULL) = (claim_expires_at IS NULL)),
  CHECK ((action_type = 'POST_CREDIT') = (credit_shipment_ref IS NOT NULL AND credit_reason_code IS NOT NULL)),
  UNIQUE (case_id, action_seq),
  -- I3 in the schema: no credit row can exist without an approval
  CHECK (action_type <> 'POST_CREDIT' OR approval_id IS NOT NULL),
  CHECK (status NOT IN ('SENT','VERIFIED') OR (sent_at IS NOT NULL AND external_ref IS NOT NULL)),
  CHECK ((status = 'VERIFIED') = (verified_at IS NOT NULL AND verified_by IS NOT NULL)),
  CHECK (status <> 'FAILED' OR (failed_at IS NOT NULL AND last_error_code IS NOT NULL)),
  CHECK (status <> 'UNKNOWN' OR unknown_at IS NOT NULL)
) STRICT;
CREATE INDEX ix_actions_in_flight ON actions (status) WHERE status IN ('PLANNED','SENT');
-- At most one credit per shipment and reason that is live or done (PLANNED, SENT, UNKNOWN, VERIFIED),
-- across all cases. Only a FAILED credit (proven not applied) frees the slot. (I3, review finding 1/5)
CREATE UNIQUE INDEX ux_actions_one_credit ON actions (credit_shipment_ref, credit_reason_code)
  WHERE action_type = 'POST_CREDIT' AND status <> 'FAILED';
CREATE INDEX ix_actions_case ON actions (case_id, action_seq);

-- Legal action status moves (executor.md section 1). VERIFIED and FAILED are final;
-- UNKNOWN -> VERIFIED | FAILED only by a human resolution (verified_by/last_error_code say so).
-- NULL-safe on purpose: with plain `=`/GLOB a NULL column makes the WHEN clause NULL, the trigger
-- does not fire, and the illegal update succeeds (found by the A1 schema check, case S21).
CREATE TRIGGER tr_actions_status BEFORE UPDATE OF status ON actions
WHEN NEW.status <> OLD.status AND NOT (
     (OLD.status = 'PLANNED' AND NEW.status IN ('SENT','FAILED','UNKNOWN'))
  OR (OLD.status = 'SENT'    AND NEW.status IN ('VERIFIED','UNKNOWN'))
  OR (OLD.status = 'UNKNOWN' AND NEW.status = 'VERIFIED' AND COALESCE(NEW.verified_by, '') GLOB 'human:*')
  OR (OLD.status = 'UNKNOWN' AND NEW.status = 'FAILED'
      AND NEW.last_error_code IS 'human_confirmed_not_done'))
BEGIN SELECT RAISE(ABORT, 'illegal_action_status_change'); END;

CREATE TRIGGER tr_actions_immutable_identity BEFORE UPDATE ON actions
WHEN NEW.idempotency_key <> OLD.idempotency_key OR NEW.payload_hash <> OLD.payload_hash
  OR NEW.intent_id <> OLD.intent_id OR NEW.action_type <> OLD.action_type
  OR NEW.approval_id IS NOT OLD.approval_id
BEGIN SELECT RAISE(ABORT, 'action_identity_is_immutable'); END;

-- ---------------------------------------------------------------------------------------------
CREATE TABLE messages (
  id                   TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'msg_*'),
  case_id              TEXT    NOT NULL REFERENCES cases(id),
  direction            TEXT    NOT NULL CHECK (direction IN ('OUT','IN')),
  action_id            TEXT    REFERENCES actions(id),
  inbox_id             TEXT    REFERENCES inbox(id),
  channel              TEXT    NOT NULL,
  external_message_id  TEXT    NOT NULL,
  internet_message_id  TEXT,
  conversation_id      TEXT,
  from_addr            TEXT    NOT NULL,
  to_addr              TEXT    NOT NULL,
  subject              TEXT    NOT NULL,
  body_hash            TEXT    NOT NULL CHECK (body_hash GLOB 'sha256:*'),
  kind                 TEXT,
  at                   TEXT    NOT NULL CHECK (at GLOB '????-??-??T??:??:??.??????Z'),
  interpreted_round    INTEGER,
  CHECK ((direction = 'OUT') = (action_id IS NOT NULL)),
  CHECK ((direction = 'IN')  = (inbox_id IS NOT NULL)),
  UNIQUE (channel, direction, external_message_id)
) STRICT;
CREATE INDEX ix_messages_conversation ON messages (conversation_id) WHERE conversation_id IS NOT NULL;
CREATE INDEX ix_messages_case ON messages (case_id, at);

-- ---------------------------------------------------------------------------------------------
CREATE TABLE timers (
  id                TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'tmr_*'),
  case_id           TEXT    NOT NULL REFERENCES cases(id),
  kind              TEXT    NOT NULL CHECK (kind IN ('SELLER_REMINDER','SELLER_DECISION','OPS_TIMEOUT',
                                                     'APPROVAL_EXPIRY','VERIFY_RETRY')),
  due_at            TEXT    NOT NULL CHECK (due_at GLOB '????-??-??T??:??:??.??????Z'),
  anchor_at         TEXT    NOT NULL CHECK (anchor_at GLOB '????-??-??T??:??:??.??????Z'),
  armed_in_state    TEXT    NOT NULL,
  armed_at_version  INTEGER NOT NULL CHECK (armed_at_version >= 1),
  correlation_id    TEXT    NOT NULL CHECK (correlation_id GLOB 'corr_*'),
  created_at        TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z'),
  fired_at          TEXT    CHECK (fired_at GLOB '????-??-??T??:??:??.??????Z'),
  consumed_at       TEXT    CHECK (consumed_at GLOB '????-??-??T??:??:??.??????Z'),  -- its TIMER_FIRED was processed (not deferred)
  voided_at         TEXT    CHECK (voided_at GLOB '????-??-??T??:??:??.??????Z'),    -- fired, then made stale by a cancel effect
  cancelled_at      TEXT    CHECK (cancelled_at GLOB '????-??-??T??:??:??.??????Z'),
  cancel_reason     TEXT,
  CHECK (NOT (fired_at IS NOT NULL AND cancelled_at IS NOT NULL)),
  CHECK (consumed_at IS NULL OR fired_at IS NOT NULL),
  CHECK (voided_at IS NULL OR (fired_at IS NOT NULL AND consumed_at IS NULL)),
  CHECK ((cancelled_at IS NULL) = (cancel_reason IS NULL)),
  CHECK (due_at >= anchor_at)
) STRICT;
-- one active timer per case and kind
CREATE UNIQUE INDEX ux_timers_active ON timers (case_id, kind) WHERE fired_at IS NULL AND cancelled_at IS NULL;
CREATE INDEX ix_timers_due ON timers (due_at) WHERE fired_at IS NULL AND cancelled_at IS NULL;

-- ---------------------------------------------------------------------------------------------
-- Outcome outbox: CaseOutcome rows delivered at least once to CaseOutcomeObserver (HOOK C).
CREATE TABLE outcome_outbox (
  id            TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'out_*'),
  case_id       TEXT    NOT NULL REFERENCES cases(id),
  state         TEXT    NOT NULL,
  resolved      INTEGER NOT NULL CHECK (resolved IN (0,1)),
  outcome_json  TEXT    NOT NULL CHECK (json_valid(outcome_json)),
  created_at    TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z'),
  delivered_at  TEXT    CHECK (delivered_at GLOB '????-??-??T??:??:??.??????Z'),
  attempts      INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error    TEXT,
  CHECK (resolved = 0 OR state IN ('RESOLVED_CREDITED','RESOLVED_UPHELD'))
) STRICT;
CREATE INDEX ix_outcomes_undelivered ON outcome_outbox (created_at) WHERE delivered_at IS NULL;

-- ---------------------------------------------------------------------------------------------
-- Runtime control flags (the kill switch can be engaged without a restart).
CREATE TABLE control_flags (
  name        TEXT NOT NULL PRIMARY KEY CHECK (name IN ('kill_switch')),
  value       TEXT NOT NULL CHECK (value IN ('0','1')),
  updated_at  TEXT NOT NULL CHECK (updated_at GLOB '????-??-??T??:??:??.??????Z'),
  updated_by  TEXT NOT NULL
) STRICT;

-- ---------------------------------------------------------------------------------------------
-- model_calls: created by the migration of the first phase that records model calls (Phase 4).
-- Listed here so the whole schema is reviewed once. No prompt or response content (I14).
CREATE TABLE model_calls (
  id               TEXT    NOT NULL PRIMARY KEY,
  case_id          TEXT    REFERENCES cases(id),
  purpose          TEXT    NOT NULL,
  route            TEXT    NOT NULL,          -- foundry_agent_service | foundry_openai | anthropic_direct | fake
  provider         TEXT    NOT NULL,
  deployment       TEXT,
  model_id         TEXT    NOT NULL,
  prompt_name      TEXT    NOT NULL,
  prompt_version   TEXT    NOT NULL,
  prompt_hash      TEXT    NOT NULL,
  input_tokens     INTEGER NOT NULL CHECK (input_tokens >= 0),
  output_tokens    INTEGER NOT NULL CHECK (output_tokens >= 0),
  latency_ms       INTEGER NOT NULL CHECK (latency_ms >= 0),
  cost_usd_micros  INTEGER NOT NULL CHECK (cost_usd_micros >= 0),
  status           TEXT    NOT NULL CHECK (status IN ('ok','invalid_output','error','blocked','budget')),
  error_code       TEXT,
  failover_from    TEXT,
  created_at       TEXT    NOT NULL CHECK (created_at GLOB '????-??-??T??:??:??.??????Z')
) STRICT;
CREATE INDEX ix_model_calls_case ON model_calls (case_id, created_at);
CREATE INDEX ix_model_calls_route_time ON model_calls (route, created_at);

-- pattern_records and pattern_case_links are created in Phase 7 only (ARCHITECTURE.md section 7).
--
-- Note on circular references: cases.pending_intent_id -> action_intents.id and
-- approvals.used_by_action_id -> actions.id would form FK cycles with the reverse links. SQLite
-- can declare them only with deferred constraints, which complicate SQLAlchemy Core migrations.
-- Decision: the repositories enforce these two references (and a repository test proves it);
-- every other reference is a real FOREIGN KEY.
