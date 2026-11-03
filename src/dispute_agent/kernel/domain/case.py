"""The facts a dispute arrives with (kernel-interfaces.md section 4.2).

Built here, in Phase 0, because two Phase 0 cards need it: `CaseOutcome` carries a
`CaseFacts` (P0-04) and `PatternContextProvider.patterns_for` takes one (P0-05). The rest of
the case model — `Case` itself, with its version and state timestamps — belongs to Phase 1,
where there is a repository to give it meaning.

"Frozen at intake" is the whole idea. Anything learned later is evidence, which is weighed,
not fact, which is assumed. Keeping the two apart in the type system is what stops a seller's
later claim from quietly becoming a premise the calculator trusts (I5).
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.values import Money, UntrustedText, require_aware

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)

# kernel-interfaces.md section 4.2: no floats, so a pack fact can never carry a value whose
# equality depends on binary rounding (I9). Decimal is how a pack states a number.
type JsonScalar = str | int | bool | Decimal | None

REF_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
Ref = Annotated[str, Field(pattern=REF_PATTERN)]

# Deliberately permissive. The kernel's job is to reject something that is plainly not an
# address, not to adjudicate RFC 5322; the mail adapter finds out what actually delivers.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CaseFacts(BaseModel):
    """The facts a dispute arrives with. Frozen at intake; later knowledge is evidence."""

    model_config = _FROZEN

    shipment_ref: Ref
    claim_ref: Ref
    seller_ref: Ref
    seller_contact: str
    raised_at: datetime
    disputed_amount: Money | None = None
    statement: UntrustedText | None = None
    pack_facts: dict[str, JsonScalar] = Field(default_factory=dict)

    @field_validator("seller_contact")
    @classmethod
    def _check_contact(cls, value: str) -> str:
        if not _EMAIL.match(value):
            raise DomainValidationError(f"seller_contact is not an email address: {value!r}")
        return value

    @field_validator("raised_at")
    @classmethod
    def _check_raised_at(cls, value: datetime) -> datetime:
        return require_aware(value, "raised_at")

    @field_validator("pack_facts", mode="before")
    @classmethod
    def _check_pack_facts(cls, value: object) -> object:
        """Catch floats explicitly, so the error names the real problem.

        Strict mode refuses a float anyway, but as a union error listing all five branches
        of JsonScalar, which tells a pack author nothing. This has to run `before` to see
        the float at all: by the `after` stage the union has already rejected it.
        """
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, float):
                    raise DomainValidationError(
                        f"pack fact {key!r} is a float; use Decimal so equality is exact (I9)"
                    )
        return value
