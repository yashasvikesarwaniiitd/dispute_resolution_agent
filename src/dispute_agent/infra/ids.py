"""IdGenerator adapters (kernel-interfaces.md section 6.1).

ULIDs rather than UUID4 for one practical reason: the first 48 bits are the timestamp, so
ids sort by creation time. An events table read in id order is in the order things happened,
and a case's ids tell you roughly when it was opened without joining anything.

Implemented here rather than taken from a library because it is twenty lines and the kernel
has a short dependency list on purpose (CLAUDE.md section 7).
"""

from __future__ import annotations

import os
import time
from typing import Final

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.ids import ID_PREFIXES

#: Crockford base32: no I, L, O or U, so a transcribed id cannot be misread as another.
_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_BITS: Final = 128
_TIMESTAMP_BITS: Final = 48
_RANDOM_BITS: Final = _ULID_BITS - _TIMESTAMP_BITS
_ULID_CHARS: Final = 26


def _encode(value: int) -> str:
    """128 bits as 26 Crockford base32 characters, most significant first."""
    out = [""] * _ULID_CHARS
    for position in range(_ULID_CHARS - 1, -1, -1):
        out[position] = _ALPHABET[value & 0x1F]
        value >>= 5
    return "".join(out)


def _check_prefix(prefix: str) -> None:
    if prefix not in ID_PREFIXES:
        raise DomainValidationError(
            f"unknown id prefix {prefix!r}; known prefixes are {sorted(ID_PREFIXES)}"
        )


class UlidIdGenerator:
    """Implements IdGenerator.

    The monotonic counter matters more than it looks. Two ids minted in the same millisecond
    would otherwise sort arbitrarily, and `EventLog.append` assigns sequence numbers by
    reading what came before: ids that tie are ids that can be read in the wrong order.
    """

    def __init__(self) -> None:
        self._last_ms = 0
        self._last_random = 0

    def new_id(self, prefix: str) -> str:
        _check_prefix(prefix)
        now_ms = time.time_ns() // 1_000_000
        if now_ms == self._last_ms:
            # Same millisecond: step the random part instead, so the id still sorts after.
            self._last_random += 1
        else:
            self._last_ms = now_ms
            self._last_random = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
        value = (now_ms << _RANDOM_BITS) | (self._last_random & ((1 << _RANDOM_BITS) - 1))
        return f"{prefix}_{_encode(value)}"


class FakeIdGenerator:
    """Implements IdGenerator. Counts, so a scenario's ids are the same on every run.

    Produces 26 digits, which `is_valid_id` accepts, so nothing downstream has to know
    whether it is talking to the real generator or this one.
    """

    def __init__(self, start: int = 1) -> None:
        self._next = start
        self.issued: list[str] = []

    def new_id(self, prefix: str) -> str:
        _check_prefix(prefix)
        value = f"{prefix}_{self._next:0{_ULID_CHARS}d}"
        self._next += 1
        self.issued.append(value)
        return value
