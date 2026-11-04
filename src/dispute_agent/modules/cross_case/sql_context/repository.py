"""PatternRepository: the cross-case module's own tables (P7-02).

Everything the module stores goes through here, and nothing here touches a kernel table except
by reading it (`cases` for ids and opening times). The module writes only its own rows:
patterns, links, outcomes and findings. External writes (the Jira investigation) never happen
here; they go through the kernel's policy engine and ActionExecutor (I2, P7-05).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from dispute_agent.infra.sqlite.mapping import from_iso, to_iso
from dispute_agent.infra.sqlite.patterns import (
    pattern_case_links,
    pattern_findings,
    pattern_outcomes,
    pattern_records,
)
from dispute_agent.infra.sqlite.tables import cases

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from datetime import datetime

    from sqlalchemy import Engine

#: Statuses a new case is briefed with. A dismissed pattern was found legitimate by ops.
OPEN_STATUSES = ("UNCONFIRMED", "CONFIRMED", "FIXED_MONITORING")


@dataclass(frozen=True)
class StoredPattern:
    """A pattern row, with the four-status vocabulary the module uses."""

    id: str
    dimension: str
    match_value: str
    status: str
    summary: str
    summary_source: str
    hypotheses: tuple[dict[str, object], ...]
    evidence_refs: tuple[str, ...]
    thresholds: dict[str, int]
    status_reason: str
    fixed_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class CaseRow:
    """What the analyzer reads about a case: who, what, when. Never the case's decisions."""

    case_id: str
    shipment_ref: str
    claim_ref: str
    seller_ref: str
    opened_at: datetime


class PatternRepository:
    """Implements the module's storage. Each method is its own short transaction."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # -- reading ---------------------------------------------------------------------------

    def matching(self, keys: Iterable[tuple[str, str]]) -> list[StoredPattern]:
        """Open patterns on any of these (dimension, value) keys, newest first."""
        wanted = [
            and_(pattern_records.c.dimension == d, pattern_records.c.match_value == v)
            for d, v in keys
        ]
        if not wanted:
            return []
        query = (
            select(pattern_records)
            .where(or_(*wanted), pattern_records.c.status.in_(OPEN_STATUSES))
            .order_by(pattern_records.c.last_seen_at.desc(), pattern_records.c.id.desc())
        )
        with self._engine.connect() as conn:
            return [_pattern(row._mapping) for row in conn.execute(query)]

    def get(self, pattern_id: str) -> StoredPattern:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(pattern_records).where(pattern_records.c.id == pattern_id)
            ).one()
        return _pattern(row._mapping)

    def find(self, dimension: str, value: str) -> StoredPattern | None:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(pattern_records).where(
                    pattern_records.c.dimension == dimension,
                    pattern_records.c.match_value == value,
                )
            ).first()
        return None if row is None else _pattern(row._mapping)

    def all(self) -> list[StoredPattern]:
        with self._engine.connect() as conn:
            rows = conn.execute(select(pattern_records).order_by(pattern_records.c.id))
            return [_pattern(row._mapping) for row in rows]

    def linked(self, pattern_id: str, role: str = "member") -> list[str]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(pattern_case_links.c.case_id)
                .where(
                    pattern_case_links.c.pattern_id == pattern_id,
                    pattern_case_links.c.role == role,
                )
                .order_by(pattern_case_links.c.case_id)
            )
            return [row[0] for row in rows]

    def case_for_claim(self, claim_ref: str) -> str | None:
        """The newest case opened for this claim, or None before intake has stored it."""
        with self._engine.connect() as conn:
            row = conn.execute(
                select(cases.c.id)
                .where(cases.c.claim_ref == claim_ref)
                .order_by(cases.c.opened_at.desc())
                .limit(1)
            ).first()
        return None if row is None else str(row[0])

    def cases_opened_between(self, start: datetime, end: datetime) -> list[CaseRow]:
        """Read-only: the analyzer's whole view of the kernel's data (P7-03)."""
        query = (
            select(
                cases.c.id,
                cases.c.shipment_ref,
                cases.c.claim_ref,
                cases.c.seller_ref,
                cases.c.opened_at,
            )
            .where(cases.c.opened_at >= to_iso(start), cases.c.opened_at <= to_iso(end))
            .order_by(cases.c.opened_at, cases.c.id)
        )
        with self._engine.connect() as conn:
            return [CaseRow(r[0], r[1], r[2], r[3], from_iso(r[4])) for r in conn.execute(query)]

    def case_row(self, case_id: str) -> CaseRow | None:
        with self._engine.connect() as conn:
            r = conn.execute(
                select(
                    cases.c.id,
                    cases.c.shipment_ref,
                    cases.c.claim_ref,
                    cases.c.seller_ref,
                    cases.c.opened_at,
                ).where(cases.c.id == case_id)
            ).first()
        return None if r is None else CaseRow(r[0], r[1], r[2], r[3], from_iso(r[4]))

    def outcomes(self) -> list[tuple[str, str, bool]]:
        """(case_id, state, resolved) for every observed outcome."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(
                    pattern_outcomes.c.case_id,
                    pattern_outcomes.c.state,
                    pattern_outcomes.c.resolved,
                ).order_by(pattern_outcomes.c.case_id)
            )
            return [(r[0], r[1], bool(r[2])) for r in rows]

    def findings(self, pattern_id: str) -> list[tuple[str, str]]:
        """(finding_ref, verdict), oldest first."""
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(pattern_findings.c.finding_ref, pattern_findings.c.verdict)
                .where(pattern_findings.c.pattern_id == pattern_id)
                .order_by(pattern_findings.c.recorded_at, pattern_findings.c.finding_ref)
            )
            return [(r[0], r[1]) for r in rows]

    # -- writing (the module's own rows only) ----------------------------------------------

    def create(
        self,
        pattern_id: str,
        *,
        dimension: str,
        value: str,
        summary: str,
        thresholds: dict[str, int],
        first_seen_at: datetime,
        last_seen_at: datetime,
        now: datetime,
    ) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                insert(pattern_records).values(
                    id=pattern_id,
                    dimension=dimension,
                    match_value=value,
                    status="UNCONFIRMED",
                    summary=summary,
                    summary_source="code",
                    hypotheses_json="[]",
                    evidence_refs_json="[]",
                    thresholds_json=json.dumps(thresholds, sort_keys=True),
                    status_reason="cluster_found",
                    first_seen_at=to_iso(first_seen_at),
                    last_seen_at=to_iso(last_seen_at),
                    created_at=to_iso(now),
                    updated_at=to_iso(now),
                )
            )

    def refresh(
        self, pattern_id: str, *, summary: str, last_seen_at: datetime, now: datetime
    ) -> None:
        """A re-run saw the cluster again. The status is never touched here."""
        with self._engine.begin() as conn:
            conn.execute(
                update(pattern_records)
                .where(pattern_records.c.id == pattern_id)
                .values(summary=summary, last_seen_at=to_iso(last_seen_at), updated_at=to_iso(now))
            )

    def set_hypotheses(
        self, pattern_id: str, hypotheses: Sequence[dict[str, object]], now: datetime
    ) -> None:
        """The pattern_analyst's competing hypotheses (P7-04). Untrusted text, stored as data."""
        with self._engine.begin() as conn:
            conn.execute(
                update(pattern_records)
                .where(pattern_records.c.id == pattern_id)
                .values(hypotheses_json=json.dumps(list(hypotheses)), updated_at=to_iso(now))
            )

    def set_status(
        self,
        pattern_id: str,
        status: str,
        reason: str,
        now: datetime,
        *,
        evidence_ref: str | None = None,
        fixed_at: datetime | None = None,
    ) -> None:
        current = self.get(pattern_id)
        refs = list(current.evidence_refs)
        if evidence_ref is not None and evidence_ref not in refs:
            refs.append(evidence_ref)
        values: dict[str, object] = {
            "status": status,
            "status_reason": reason,
            "evidence_refs_json": json.dumps(refs),
            "updated_at": to_iso(now),
        }
        if fixed_at is not None:
            values["fixed_at"] = to_iso(fixed_at)
        with self._engine.begin() as conn:
            conn.execute(
                update(pattern_records).where(pattern_records.c.id == pattern_id).values(**values)
            )

    def link(self, pattern_id: str, case_ids: Iterable[str], role: str, now: datetime) -> int:
        """Idempotent. Returns how many links were new."""
        added = 0
        with self._engine.begin() as conn:
            for case_id in case_ids:
                result = conn.execute(
                    sqlite_insert(pattern_case_links)
                    .values(
                        pattern_id=pattern_id, case_id=case_id, role=role, linked_at=to_iso(now)
                    )
                    .on_conflict_do_nothing()
                )
                added += result.rowcount or 0
        return added

    def record_outcome(
        self,
        outcome_id: str,
        case_id: str,
        state: str,
        resolved: bool,
        credited_paise: int | None,
        closed_at: datetime,
        now: datetime,
    ) -> bool:
        """Idempotent on the outcome id (HOOK C is at least once). True if it was new."""
        with self._engine.begin() as conn:
            result = conn.execute(
                sqlite_insert(pattern_outcomes)
                .values(
                    outcome_id=outcome_id,
                    case_id=case_id,
                    state=state,
                    resolved=1 if resolved else 0,
                    credited_paise=credited_paise,
                    closed_at=to_iso(closed_at),
                    observed_at=to_iso(now),
                )
                .on_conflict_do_nothing()
            )
        return bool(result.rowcount)

    def record_finding(
        self, pattern_id: str, finding_ref: str, verdict: str, now: datetime
    ) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                sqlite_insert(pattern_findings)
                .values(
                    pattern_id=pattern_id,
                    finding_ref=finding_ref,
                    verdict=verdict,
                    recorded_at=to_iso(now),
                )
                .on_conflict_do_nothing()
            )
        return bool(result.rowcount)


def _pattern(row: object) -> StoredPattern:
    m: dict[str, object] = dict(row)  # type: ignore[call-overload]  # a RowMapping
    return StoredPattern(
        id=str(m["id"]),
        dimension=str(m["dimension"]),
        match_value=str(m["match_value"]),
        status=str(m["status"]),
        summary=str(m["summary"]),
        summary_source=str(m["summary_source"]),
        hypotheses=tuple(json.loads(str(m["hypotheses_json"]))),
        evidence_refs=tuple(json.loads(str(m["evidence_refs_json"]))),
        thresholds=dict(json.loads(str(m["thresholds_json"]))),
        status_reason=str(m["status_reason"]),
        fixed_at=None if m["fixed_at"] is None else from_iso(str(m["fixed_at"])),
        first_seen_at=from_iso(str(m["first_seen_at"])),
        last_seen_at=from_iso(str(m["last_seen_at"])),
    )
