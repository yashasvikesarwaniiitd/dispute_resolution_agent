"""Canonical JSON and payload hashes (kernel-interfaces.md section 2.3).

Every comparison of "the text approved" against "the text sent" (I13) and every
idempotency key (executor.md section 2) goes through `payload_hash`. Two payloads are the
same if and only if their hashes are equal, so the encoding must be deterministic: the same
value must always produce the same bytes, on any machine, in any Python run.

Floats are rejected rather than encoded. A float cannot represent 0.1 exactly, so hashing
one would make equality depend on binary rounding — exactly the class of bug I9 exists to
prevent. Money and Weight carry Decimal.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import PayloadHash


def _encode(value: Any) -> Any:
    """Convert one value into something json.dumps can encode deterministically."""
    if isinstance(value, bool) or value is None or isinstance(value, str | int):
        return value
    if isinstance(value, float):
        raise DomainValidationError(
            "floats cannot be hashed: use Decimal (Money, Weight) so equality is exact"
        )
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DomainValidationError(f"not a finite Decimal: {value}")
        return str(value)
    if isinstance(value, Enum):
        return _encode(value.value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise DomainValidationError("naive datetime; all times are timezone-aware UTC")
        # ISO 8601 UTC with "Z" and microseconds. Normalising to UTC first means two
        # instants that are equal hash equally, whatever offset they arrived in.
        utc = value.astimezone(UTC)
        return utc.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, BaseModel):
        return _encode(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_encode(v) for v in value]
    raise DomainValidationError(f"cannot canonicalise {type(value).__name__}")


def canonical_json(value: object) -> str:
    """Deterministic JSON: keys sorted, no whitespace, UTF-8, non-ASCII kept as-is."""
    return json.dumps(
        _encode(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def payload_hash(value: object) -> PayloadHash:
    """'sha256:' + the hex sha256 of canonical_json(value) encoded as UTF-8."""
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return PayloadHash(f"sha256:{digest}")
