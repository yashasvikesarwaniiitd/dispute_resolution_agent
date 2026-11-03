"""The IdGenerator port (kernel-interfaces.md section 6.1).

Ids come from a port for the same reason time does: a test needs them predictable, and the
kernel needs them unique without knowing how they are made.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class IdGenerator(Protocol):
    def new_id(self, prefix: str) -> str:
        """Pre: prefix in ID_PREFIXES. Post: globally unique, matching the prefixed ULID form.

        Raises DomainValidationError for an unknown prefix.
        """
        ...
