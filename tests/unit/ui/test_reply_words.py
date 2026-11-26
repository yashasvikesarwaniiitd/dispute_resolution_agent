"""A seller's reply on the screens shows their own words, never our quoted email (live,
SHP-102026, 2026-11-27: a photo-only reply showed the thread it answered)."""

from __future__ import annotations

from datetime import UTC, datetime

from dispute_agent.kernel.domain.enums import EvidenceStatus, Provenance
from dispute_agent.kernel.domain.evidence import EvidenceItem, EvidenceResult
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import CaseId, EvidenceItemId
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.ui.came_back import seller_reply
from tests.unit.domain.test_mailbox import PHOTO_ONLY


def reply_item(body: str) -> EvidenceItem:
    result = EvidenceResult(
        requirement_key="seller_reply",
        source="seller_email",
        status=EvidenceStatus.FOUND,
        value={"intent": "provides_requested_evidence", "summary": "A photo was attached."},
        untrusted=(UntrustedText.create(body, "seller_email", "m1"),),
        retrieved_at=datetime(2026, 11, 27, tzinfo=UTC),
    )
    return EvidenceItem(
        id=EvidenceItemId("evi_1"),
        case_id=CaseId("case_1"),
        gather_round=2,
        provenance=Provenance.SELLER,
        result=result,
        content_hash=payload_hash(result.model_dump(mode="json")),
    )


def test_a_photo_only_reply_shows_no_quoted_email() -> None:
    read, words = seller_reply(reply_item(PHOTO_ONLY))
    assert words == ()
    assert "wrote no message of their own" in read


def test_the_seller_s_own_words_are_shown_without_the_quote() -> None:
    body = f"Box is 21 x 13 x 4 cm.\n\n{PHOTO_ONLY}"
    _, words = seller_reply(reply_item(body))
    assert words == ("Box is 21 x 13 x 4 cm.",)
