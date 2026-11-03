"""Idempotency keys (executor.md section 2, ADR 0007).

The key is derived once, stored on the action row, and **never recomputed**. Every retry
reads it back, so a change to this formula cannot turn a retry into a second write. That is
what `wd1:` is for: a new derivation gets a new prefix and old keys keep meaning what they
meant.

`action_seq` is in the formula for a reason worth remembering. The ARCHITECTURE sketch hashed
case, type and payload. Two legitimately separate actions with identical payloads — the same
evidence request sent again after a reopen — would then share a key, and the second would be
silently swallowed as a duplicate of the first. The sequence number makes a new action a new
key while a retry of one action keeps its own.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import IdempotencyKey

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.enums import ActionType
    from dispute_agent.kernel.domain.ids import CaseId, PayloadHash

#: Versions the derivation. Changing the formula means changing this.
PREFIX = "wd1:"


def idempotency_key(
    case_id: CaseId, action_type: ActionType, action_seq: int, payload_hash: PayloadHash
) -> IdempotencyKey:
    """`wd1:` + sha256(case|type|seq|payload_hash). 68 characters, as schema.sql checks."""
    if action_seq < 1:
        raise DomainValidationError(f"action_seq starts at 1, not {action_seq}")
    raw = f"{case_id}|{action_type.value}|{action_seq}|{payload_hash}"
    return IdempotencyKey(PREFIX + hashlib.sha256(raw.encode("utf-8")).hexdigest())


def jira_label(key: IdempotencyKey) -> str:
    """Jira labels cannot contain ':', so the marker is the first 40 hex characters.

    The full key goes in an issue property. The label is for finding; the property is for
    proving, and the two are not interchangeable (integrations.md section 2).
    """
    return "wd1-" + str(key).removeprefix(PREFIX)[:40]
