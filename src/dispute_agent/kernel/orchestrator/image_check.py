"""The image check round: after a person returns a photo case, a vision model looks (ADR 0049).

A round whose evidence carries the hub's photo flag goes to a person (baseline row B5: photos
need a person's judgement). When that person returns the case to the agent, the new round
would reach the same row and come straight back. This gives the returned round something new:
the image check's verdict on the photo, recorded as one more evidence item.

**When.** Only in a round started by a person's Return (HUMAN_RETURNED_CASE, T37), and only
when the round's evidence carries the photo flag. That is one paid call per Return.

**What it can change.** Nothing it should not. The verdict is evidence for a person to read:
B5 still sends the case to a person, and no rule reads the verdict to approve, credit or close
anything (I1, I7). Its observations are model-written, so they are stored as `UntrustedText`
(I5); its closed choices are the item's value.

**When it fails.** The failure is written down as a FAILED item with a `SYSTEM` provenance and
an `image_check_failed:<code>` error - never dropped, never a guess (I4) - and the case still
goes to a person. It is not stored as an AI reading, because B2 reads a failed AI reading as
"a reply could not be read", which would tell the person the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import (
    EvidenceStatus,
    InboundEventType,
    InternalEventType,
    Provenance,
)
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.evidence import EvidenceItem, EvidenceResult
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import EvidenceItemId
from dispute_agent.kernel.domain.image import ImageCheckRequest
from dispute_agent.kernel.domain.values import UntrustedText

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.events import Event
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.ports import (
        EventLog,
        EvidenceStore,
        IdGenerator,
        ImageChecker,
        PhotoSource,
    )

#: The requirement key the verdict is stored under. Not in the pack's evidence plan: the
#: check is not something we fetch, it is something we ask after a person returns a case.
IMAGE_CHECK = "parcel.image_check"
SOURCE = "image_check"


@dataclass
class ImageCheckRound:
    events: EventLog
    evidence: EvidenceStore
    ids: IdGenerator
    checker: ImageChecker
    photos: PhotoSource
    #: The pack's key for "this round carries the hub's photo flag" (the row B5 reads).
    image_key: str
    #: The pack's declared and measured size, rendered from this round's evidence.
    size_facts: Callable[[Sequence[EvidenceResult]], tuple[str, str]]

    def read(self, case: Case, now: datetime, results: Sequence[EvidenceResult]) -> bool:
        """Check the photo when this round was started by a Return. True when it ran."""
        flagged = any(
            r.requirement_key == self.image_key and r.status is EvidenceStatus.FOUND
            for r in results
        )
        if not flagged or not after_a_return(self.events.list_for_case(case.id)):
            return False
        photo = self.photos.photo(case.facts.shipment_ref)
        if photo is None:
            self.evidence.add(self._failed(case, now, "image_check_failed:no_photo"))
            return True
        declared, measured = self.size_facts(results)
        try:
            verdict = self.checker.check(
                ImageCheckRequest(
                    case_id=case.id,
                    shipment_ref=case.facts.shipment_ref,
                    photo=photo,
                    declared=declared,
                    measured=measured,
                )
            )
        except KernelError as failed:
            self.evidence.add(self._failed(case, now, f"image_check_failed:{failed.code}"))
            return True
        value = verdict.model_dump(mode="json", exclude={"observations"}) | {
            "photo_sha256": photo.sha256
        }
        notes = tuple(
            UntrustedText.create(line, "image_check", f"{case.id}:{case.gather_round}:{i}")
            for i, line in enumerate(verdict.observations)
        )
        result = EvidenceResult(
            requirement_key=IMAGE_CHECK,
            source=SOURCE,
            status=EvidenceStatus.FOUND,
            value=value,
            untrusted=notes,
            retrieved_at=now,
        )
        self.evidence.add(self._item(case, result, Provenance.AI_DERIVED))
        return True

    def _failed(self, case: Case, now: datetime, code: str) -> EvidenceItem:
        result = EvidenceResult(
            requirement_key=IMAGE_CHECK,
            source=SOURCE,
            status=EvidenceStatus.FAILED,
            error_code=code,
            retrieved_at=now,
        )
        return self._item(case, result, Provenance.SYSTEM)

    def _item(self, case: Case, result: EvidenceResult, provenance: Provenance) -> EvidenceItem:
        return EvidenceItem(
            id=EvidenceItemId(self.ids.new_id("evi")),
            case_id=case.id,
            gather_round=case.gather_round,
            provenance=provenance,
            result=result,
            content_hash=payload_hash(result.model_dump(mode="json")),
        )


def after_a_return(events: Sequence[Event]) -> bool:
    """Whether the round being gathered now was started by a person's Return.

    True when the last HUMAN_RETURNED_CASE has no gather step recorded after it: the gather
    happening now is the first since that Return.
    """
    last = max(
        (i for i, e in enumerate(events) if e.type == InboundEventType.HUMAN_RETURNED_CASE),
        default=None,
    )
    if last is None:
        return False
    return not any(
        e.type == InternalEventType.STATE_CHANGED
        and e.cause.kind.value == "STEP"
        and e.cause.ref == "gather_evidence"
        for e in events[last + 1 :]
    )


__all__ = ["IMAGE_CHECK", "SOURCE", "ImageCheckRound", "after_a_return"]
