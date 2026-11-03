"""Identifier types (kernel-interfaces.md section 1).

Each id is a NewType over str with a fixed prefix. NewType gives mypy a distinct type at
no runtime cost, so passing a CaseId where an ActionId is expected is a type error rather
than a bug found in production. `validate_id` is for boundaries where the value arrives as
a plain string (a request body, a database row).
"""

from __future__ import annotations

import re
from typing import Final, NewType

CaseId = NewType("CaseId", str)
EventId = NewType("EventId", str)
InboxId = NewType("InboxId", str)
EvidenceItemId = NewType("EvidenceItemId", str)
AssessmentId = NewType("AssessmentId", str)
ProposalId = NewType("ProposalId", str)
IntentId = NewType("IntentId", str)
VerdictId = NewType("VerdictId", str)
ApprovalId = NewType("ApprovalId", str)
ActionId = NewType("ActionId", str)
MessageId = NewType("MessageId", str)
TimerId = NewType("TimerId", str)
OutcomeId = NewType("OutcomeId", str)
PatternId = NewType("PatternId", str)
CorrelationId = NewType("CorrelationId", str)

DedupeKey = NewType("DedupeKey", str)
IdempotencyKey = NewType("IdempotencyKey", str)
PayloadHash = NewType("PayloadHash", str)

ID_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        "case",
        "evt",
        "inb",
        "evi",
        "asm",
        "prp",
        "int",
        "vrd",
        "apr",
        "act",
        "msg",
        "tmr",
        "out",
        "pat",
        "corr",
        "mcl",  # model_calls rows (P4b-02)
    }
)

#: Crockford base32, 26 characters: what IdGenerator.new_id produces for a real ULID.
#: The fake generator produces 26 digits, which this also accepts, so tests use the same checks.
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_IDEMPOTENCY_KEY = re.compile(r"^wd1:[0-9a-f]{64}$")
_PAYLOAD_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


def id_prefix(value: str) -> str:
    """The prefix of an id, or "" when it has none."""
    head, sep, _ = value.partition("_")
    return head if sep else ""


def is_valid_id(value: str, prefix: str) -> bool:
    """True when `value` is `<prefix>_<26 Crockford base32 chars>`."""
    if prefix not in ID_PREFIXES:
        return False
    head, sep, body = value.partition("_")
    return bool(sep) and head == prefix and bool(_ULID.match(body))


def is_valid_idempotency_key(value: str) -> bool:
    """executor.md section 2: "wd1:" + 64 hex characters."""
    return bool(_IDEMPOTENCY_KEY.match(value))


def is_valid_payload_hash(value: str) -> bool:
    """kernel-interfaces.md section 2.3: "sha256:" + 64 hex characters."""
    return bool(_PAYLOAD_HASH.match(value))
