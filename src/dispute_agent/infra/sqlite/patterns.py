"""The cross-case tables (Phase 7, P7-01; ARCHITECTURE.md section 7).

`schema.sql` is frozen and says these tables "are created in Phase 7 only", so they live here
and are created by migration 0002, never by editing the kernel schema. The conventions are
schema.sql's own: STRICT tables, "<prefix>_<ULID>" ids, ISO 8601 UTC text with microseconds
and a "Z", JSON checked with json_valid(), and CHECK constraints for every closed vocabulary.

Six tables, where the card names two. The other four keep the module honest:

- `pattern_records`: one hypothesis per cluster key, with the thresholds that produced it.
- `pattern_case_links`: `member` (the case is part of the cluster) and `informed` (the
  pattern was shown to the case). The second role is what makes "a case decided with the
  pattern's help never confirms it" checkable: the kernel's CaseOutcome cannot name the
  patterns it used (no kernel change in Phase 7), so the module records it itself.
- `pattern_outcomes`: closed cases handed over by the CaseOutcomeObserver, idempotent on the
  outcome id (HOOK C is at least once).
- `pattern_findings`: independent evidence from operations. Only these move a status.
- `pattern_investigations` and `pattern_actions`: the parent Jira issue and its action rows,
  kept by the module's own ActionStore so the kernel ActionExecutor can plan, execute and
  verify it without a case row (ADR 0036).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, Integer, MetaData, Table, Text

if TYPE_CHECKING:
    from sqlalchemy import Engine

_TS = "GLOB '????-??-??T??:??:??.??????Z'"

#: The four statuses (card P7-01). The kernel's PatternRecord knows three; FIXED_MONITORING
#: is shown to the kernel as CONFIRMED, which is what it is - a confirmed cause under watch.
STATUSES = ("UNCONFIRMED", "CONFIRMED", "DISMISSED", "FIXED_MONITORING")
DIMENSIONS = ("scanner", "hub", "sku", "seller")
LINK_ROLES = ("member", "informed")
FINDING_VERDICTS = ("fault_confirmed", "no_fault_found", "fix_deployed")

PATTERNS_DDL = f"""
CREATE TABLE IF NOT EXISTS pattern_records (
  id                 TEXT    NOT NULL PRIMARY KEY CHECK (id GLOB 'pat_*'),
  dimension          TEXT    NOT NULL CHECK (dimension IN {DIMENSIONS!r}),
  match_value        TEXT    NOT NULL CHECK (length(match_value) BETWEEN 1 AND 100),
  status             TEXT    NOT NULL CHECK (status IN {STATUSES!r}),
  summary            TEXT    NOT NULL CHECK (length(summary) BETWEEN 1 AND 400),
  summary_source     TEXT    NOT NULL CHECK (summary_source IN ('code','model')),
  hypotheses_json    TEXT    NOT NULL DEFAULT '[]'
                             CHECK (json_valid(hypotheses_json) AND json_type(hypotheses_json) = 'array'),
  evidence_refs_json TEXT    NOT NULL DEFAULT '[]'
                             CHECK (json_valid(evidence_refs_json) AND json_type(evidence_refs_json) = 'array'),
  thresholds_json    TEXT    NOT NULL
                             CHECK (json_valid(thresholds_json) AND json_type(thresholds_json) = 'object'),
  status_reason      TEXT    NOT NULL,
  fixed_at           TEXT    CHECK (fixed_at {_TS}),
  first_seen_at      TEXT    NOT NULL CHECK (first_seen_at {_TS}),
  last_seen_at       TEXT    NOT NULL CHECK (last_seen_at {_TS}),
  created_at         TEXT    NOT NULL CHECK (created_at {_TS}),
  updated_at         TEXT    NOT NULL CHECK (updated_at {_TS}),
  CHECK (last_seen_at >= first_seen_at),
  CHECK (status <> 'FIXED_MONITORING' OR fixed_at IS NOT NULL),
  UNIQUE (dimension, match_value)
) STRICT;
CREATE INDEX IF NOT EXISTS ix_pattern_records_status ON pattern_records (status, last_seen_at);

CREATE TABLE IF NOT EXISTS pattern_case_links (
  pattern_id  TEXT NOT NULL REFERENCES pattern_records(id),
  case_id     TEXT NOT NULL REFERENCES cases(id),
  role        TEXT NOT NULL CHECK (role IN {LINK_ROLES!r}),
  linked_at   TEXT NOT NULL CHECK (linked_at {_TS}),
  PRIMARY KEY (pattern_id, case_id, role)
) STRICT, WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_pattern_links_case ON pattern_case_links (case_id, role);

CREATE TABLE IF NOT EXISTS pattern_outcomes (
  outcome_id     TEXT    NOT NULL PRIMARY KEY CHECK (outcome_id GLOB 'out_*'),
  case_id        TEXT    NOT NULL REFERENCES cases(id),
  state          TEXT    NOT NULL,
  resolved       INTEGER NOT NULL CHECK (resolved IN (0,1)),
  credited_paise INTEGER CHECK (credited_paise IS NULL OR credited_paise > 0),
  closed_at      TEXT    NOT NULL CHECK (closed_at {_TS}),
  observed_at    TEXT    NOT NULL CHECK (observed_at {_TS})
) STRICT;
CREATE INDEX IF NOT EXISTS ix_pattern_outcomes_case ON pattern_outcomes (case_id);

CREATE TABLE IF NOT EXISTS pattern_findings (
  pattern_id   TEXT NOT NULL REFERENCES pattern_records(id),
  finding_ref  TEXT NOT NULL CHECK (length(finding_ref) BETWEEN 1 AND 100),
  verdict      TEXT NOT NULL CHECK (verdict IN {FINDING_VERDICTS!r}),
  recorded_at  TEXT NOT NULL CHECK (recorded_at {_TS}),
  PRIMARY KEY (pattern_id, finding_ref)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS pattern_investigations (
  pattern_id    TEXT NOT NULL PRIMARY KEY REFERENCES pattern_records(id),
  intent_json   TEXT NOT NULL CHECK (json_valid(intent_json)),
  action_id     TEXT CHECK (action_id GLOB 'act_*'),
  external_ref  TEXT,
  status        TEXT NOT NULL CHECK (status IN ('PROPOSED','REFUSED','PLANNED','SENT','VERIFIED','FAILED','UNKNOWN')),
  status_reason TEXT,
  opened_at     TEXT NOT NULL CHECK (opened_at {_TS}),
  updated_at    TEXT NOT NULL CHECK (updated_at {_TS}),
  CHECK (status NOT IN ('SENT','VERIFIED') OR external_ref IS NOT NULL)
) STRICT;

CREATE TABLE IF NOT EXISTS pattern_actions (
  id              TEXT NOT NULL PRIMARY KEY CHECK (id GLOB 'act_*'),
  pattern_id      TEXT NOT NULL REFERENCES pattern_records(id),
  idempotency_key TEXT NOT NULL UNIQUE CHECK (idempotency_key GLOB 'wd1:*' AND length(idempotency_key) = 68),
  action_type     TEXT NOT NULL CHECK (action_type = 'UPSERT_ISSUE'),
  status          TEXT NOT NULL CHECK (status IN ('PLANNED','SENT','VERIFIED','FAILED','UNKNOWN')),
  action_json     TEXT NOT NULL CHECK (json_valid(action_json)),
  updated_at      TEXT NOT NULL CHECK (updated_at {_TS})
) STRICT;
"""

#: Dropped in reverse dependency order by the migration's downgrade.
TABLES: tuple[str, ...] = (
    "pattern_actions",
    "pattern_investigations",
    "pattern_findings",
    "pattern_outcomes",
    "pattern_case_links",
    "pattern_records",
)


def apply_pattern_schema(engine: Engine) -> None:
    """Create the cross-case tables on a database that already has the kernel schema.

    Idempotent (IF NOT EXISTS), so the runtime can call it on every start, the same way the
    migration does once. Executed verbatim, for the reason `apply_schema` gives.
    """
    raw = engine.raw_connection()
    try:
        raw.executescript(PATTERNS_DDL)
        raw.commit()
    finally:
        raw.close()


# -- SQLAlchemy Core descriptions, for querying (a separate MetaData, so the kernel schema's
#    conformance test keeps comparing exactly the tables schema.sql creates) --------------------

pattern_metadata = MetaData()

pattern_records = Table(
    "pattern_records",
    pattern_metadata,
    Column("id", Text, primary_key=True),
    Column("dimension", Text, nullable=False),
    Column("match_value", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("summary", Text, nullable=False),
    Column("summary_source", Text, nullable=False),
    Column("hypotheses_json", Text, nullable=False),
    Column("evidence_refs_json", Text, nullable=False),
    Column("thresholds_json", Text, nullable=False),
    Column("status_reason", Text, nullable=False),
    Column("fixed_at", Text),
    Column("first_seen_at", Text, nullable=False),
    Column("last_seen_at", Text, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

pattern_case_links = Table(
    "pattern_case_links",
    pattern_metadata,
    Column("pattern_id", Text, primary_key=True),
    Column("case_id", Text, primary_key=True),
    Column("role", Text, primary_key=True),
    Column("linked_at", Text, nullable=False),
)

pattern_outcomes = Table(
    "pattern_outcomes",
    pattern_metadata,
    Column("outcome_id", Text, primary_key=True),
    Column("case_id", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("resolved", Integer, nullable=False),
    Column("credited_paise", Integer),
    Column("closed_at", Text, nullable=False),
    Column("observed_at", Text, nullable=False),
)

pattern_findings = Table(
    "pattern_findings",
    pattern_metadata,
    Column("pattern_id", Text, primary_key=True),
    Column("finding_ref", Text, primary_key=True),
    Column("verdict", Text, nullable=False),
    Column("recorded_at", Text, nullable=False),
)

pattern_investigations = Table(
    "pattern_investigations",
    pattern_metadata,
    Column("pattern_id", Text, primary_key=True),
    Column("intent_json", Text, nullable=False),
    Column("action_id", Text),
    Column("external_ref", Text),
    Column("status", Text, nullable=False),
    Column("status_reason", Text),
    Column("opened_at", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)

pattern_actions = Table(
    "pattern_actions",
    pattern_metadata,
    Column("id", Text, primary_key=True),
    Column("pattern_id", Text, nullable=False),
    Column("idempotency_key", Text, nullable=False),
    Column("action_type", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("action_json", Text, nullable=False),
    Column("updated_at", Text, nullable=False),
)
