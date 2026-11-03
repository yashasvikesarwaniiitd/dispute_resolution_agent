"""EvidenceStore and AssessmentStore on SQLite (kernel-interfaces.md section 6.2).

Both are used only inside a unit of work, so a round's items and the assessment computed
from them commit with the case row or not at all.

Two things are stored as JSON rather than as columns, on purpose. An evidence `value` is
whatever its source returned and its shape belongs to the source, not to this schema; and
`untrusted` travels with the item so that free text cannot be separated from the label
saying where it came from (I5). Numbers inside both are written as strings, because a
Decimal that goes through JSON as a float comes back as 30.000000000000004 and the slab it
lands in is a seller's money (ADR 0011).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from dispute_agent.infra.sqlite.mapping import from_iso, to_iso
from dispute_agent.infra.sqlite.tables import assessments as assessments_table
from dispute_agent.infra.sqlite.tables import evidence_items as items_table
from dispute_agent.kernel.domain.enums import EvidenceStatus, Provenance
from dispute_agent.kernel.domain.errors import NotFound
from dispute_agent.kernel.domain.evidence import (
    ChargeAssessment,
    EvidenceBundle,
    EvidenceItem,
    EvidenceResult,
    Fact,
)
from dispute_agent.kernel.domain.ids import EvidenceItemId, PayloadHash
from dispute_agent.kernel.domain.values import Money, UntrustedText

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.ports import Clock


def _dumps(value: object) -> str:
    """Canonical JSON: sorted keys, Decimals as strings, no floats anywhere."""
    return json.dumps(value, sort_keys=True, default=str)


class SqliteEvidenceStore:
    """Implements EvidenceStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, item: EvidenceItem) -> None:
        result = item.result
        self._conn.execute(
            items_table.insert().values(
                id=str(item.id),
                case_id=str(item.case_id),
                gather_round=item.gather_round,
                requirement_key=result.requirement_key,
                source=result.source,
                provenance=item.provenance.value,
                status=result.status.value,
                value_json=_dumps(result.value) if result.value is not None else None,
                untrusted_json=_dumps([text.model_dump(mode="json") for text in result.untrusted]),
                error_code=result.error_code,
                retrieved_at=to_iso(result.retrieved_at),
                content_hash=str(item.content_hash),
            )
        )

    def bundle(self, case_id: CaseId, gather_round: int) -> EvidenceBundle:
        rows = (
            self._conn.execute(
                select(items_table)
                .where(items_table.c.case_id == str(case_id))
                .where(items_table.c.gather_round == gather_round)
                .order_by(items_table.c.id)
            )
            .mappings()
            .all()
        )
        if not rows:
            # Not an empty bundle. A round with nothing in it means the gather step never
            # ran, and an empty bundle would be assessed as though every source had
            # answered "nothing there".
            raise NotFound(f"no evidence for case {case_id} round {gather_round}")
        return EvidenceBundle(
            case_id=case_id,
            gather_round=gather_round,
            items=tuple(_to_item(row) for row in rows),
        )


class SqliteAssessmentStore:
    """Implements AssessmentStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection, *, pack: str, clock: Clock) -> None:
        self._conn = connection
        self._pack = pack
        self._clock = clock

    def add(self, assessment: ChargeAssessment) -> None:
        self._conn.execute(
            assessments_table.insert().values(
                id=str(assessment.id),
                case_id=str(assessment.case_id),
                gather_round=assessment.gather_round,
                pack=self._pack,
                pack_version=assessment.pack_version,
                eligible=assessment.eligible,
                charged_paise=_paise(assessment.charged),
                recalculated_paise=_paise(assessment.recalculated),
                discrepancy_paise=_paise(assessment.discrepancy),
                result_json=_dumps(assessment.model_dump(mode="json")),
                input_hash=str(assessment.input_hash),
                created_at=to_iso(self._clock.now()),
            )
        )

    def latest(self, case_id: CaseId) -> ChargeAssessment | None:
        row = (
            self._conn.execute(
                select(assessments_table.c.result_json)
                .where(assessments_table.c.case_id == str(case_id))
                .order_by(assessments_table.c.gather_round.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _to_assessment(json.loads(row["result_json"]))


def _to_assessment(raw: dict[str, Any]) -> ChargeAssessment:
    """Rebuild the assessment from its stored JSON.

    The sequence fields are re-tupled by hand because the domain models are strict: JSON
    has only lists, and a strict model refuses one where it declared a tuple. Coercing here
    rather than loosening the model keeps "a stored assessment is immutable" true, which is
    the property R09 leans on when a message cites its numbers.
    """
    facts = tuple(
        Fact.model_validate({**fact, "source_items": tuple(fact.get("source_items", ()))})
        for fact in raw.get("facts", ())
    )
    return ChargeAssessment.model_validate(
        {
            **raw,
            "reason_codes": tuple(raw.get("reason_codes", ())),
            "definite_errors": tuple(raw.get("definite_errors", ())),
            "facts": facts,
        }
    )


def _paise(amount: Money | None) -> int | None:
    return None if amount is None else amount.to_paise()


def _to_item(row: Any) -> EvidenceItem:
    value = json.loads(row["value_json"]) if row["value_json"] else None
    untrusted = tuple(
        UntrustedText.model_validate(entry) for entry in json.loads(row["untrusted_json"])
    )
    return EvidenceItem(
        id=EvidenceItemId(row["id"]),
        case_id=row["case_id"],
        gather_round=row["gather_round"],
        provenance=Provenance(row["provenance"]),
        result=EvidenceResult(
            requirement_key=row["requirement_key"],
            source=row["source"],
            status=EvidenceStatus(row["status"]),
            value=value,
            untrusted=untrusted,
            error_code=row["error_code"],
            retrieved_at=from_iso(row["retrieved_at"]),
        ),
        content_hash=PayloadHash(row["content_hash"]),
    )


__all__ = ["SqliteAssessmentStore", "SqliteEvidenceStore"]
