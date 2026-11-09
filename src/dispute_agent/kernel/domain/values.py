"""Value objects (kernel-interfaces.md section 2).

Money and Weight exist so that a rounding decision cannot be made by accident. Inside the
kernel there is no rounding, ever: a value with too many decimal places is a validation
error, so the only place a rounding rule can live is the domain pack, explicitly
(money-rules.md). That is what I9 asks for.
"""

from __future__ import annotations

from decimal import Decimal, DecimalException, InvalidOperation
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator

from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import PayloadHash

if TYPE_CHECKING:
    from datetime import datetime

_FROZEN = ConfigDict(frozen=True, extra="forbid", strict=True)


def require_aware(value: datetime, what: str) -> datetime:
    """Refuse a naive datetime.

    Every time in this system is an instant, not a wall-clock reading. A naive datetime has
    no instant until someone guesses a zone, and the guess is usually the server's, which is
    how a timer fires an hour early in one deployment and not another. `hashing.py` refuses
    them for the same reason, so the two agree.
    """
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DomainValidationError(f"{what} is naive; all times are timezone-aware")
    return value


def _to_decimal(value: str | int | Decimal, places: int, what: str) -> Decimal:
    """Parse to a Decimal with exactly `places` decimal places, or raise.

    float is rejected before it can lose precision; str and Decimal must already carry at
    most `places` places, and are then quantized so "12.5" and "12.50" compare equal.
    """
    if isinstance(value, bool | float):
        raise DomainValidationError(f"{what} must not come from a float: {value!r}")
    try:
        dec = Decimal(value) if not isinstance(value, Decimal) else value
    except (InvalidOperation, DecimalException, ValueError) as exc:
        raise DomainValidationError(f"{what} is not a number: {value!r}") from exc
    if not dec.is_finite():
        raise DomainValidationError(f"{what} must be finite: {value!r}")
    if dec < 0:
        raise DomainValidationError(f"{what} must not be negative: {value!r}")
    if -dec.as_tuple().exponent > places:  # type: ignore[operator]
        raise DomainValidationError(f"{what} has more than {places} decimal places: {value!r}")
    return dec.quantize(Decimal(1).scaleb(-places))


class Money(BaseModel):
    """An exact INR amount. Never negative; direction is expressed by the owner."""

    model_config = _FROZEN

    amount: Decimal
    currency: Literal["INR"] = "INR"

    @field_validator("amount", mode="before")
    @classmethod
    def _check(cls, value: object) -> Decimal:
        if isinstance(value, str | int | Decimal):
            return _to_decimal(value, 2, "money")
        raise DomainValidationError(f"money must be str, int or Decimal: {value!r}")

    @classmethod
    def of(cls, value: str | int | Decimal) -> Self:
        return cls(amount=_to_decimal(value, 2, "money"))

    @classmethod
    def from_paise(cls, paise: int) -> Self:
        if isinstance(paise, bool) or not isinstance(paise, int):
            raise DomainValidationError(f"paise must be an int: {paise!r}")
        return cls(amount=(Decimal(paise) / 100).quantize(Decimal("0.01")))

    def to_paise(self) -> int:
        return int(self.amount.scaleb(2))

    def __add__(self, other: Money) -> Money:
        return Money(amount=self.amount + other.amount)

    def __sub__(self, other: Money) -> Money:
        result = self.amount - other.amount
        if result < 0:
            raise DomainValidationError(f"money cannot go negative: {self.amount} - {other.amount}")
        return Money(amount=result)

    def __lt__(self, other: Money) -> bool:
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"INR {self.amount}"


class Weight(BaseModel):
    """Kilograms with exactly three decimal places (gram precision). Never negative."""

    model_config = _FROZEN

    kg: Decimal

    @field_validator("kg", mode="before")
    @classmethod
    def _check(cls, value: object) -> Decimal:
        if isinstance(value, str | int | Decimal):
            return _to_decimal(value, 3, "weight")
        raise DomainValidationError(f"weight must be str, int or Decimal: {value!r}")

    @classmethod
    def of(cls, value: str | int | Decimal) -> Self:
        return cls(kg=_to_decimal(value, 3, "weight"))

    @classmethod
    def from_grams(cls, grams: int) -> Self:
        if isinstance(grams, bool) or not isinstance(grams, int):
            raise DomainValidationError(f"grams must be an int: {grams!r}")
        return cls(kg=(Decimal(grams) / 1000).quantize(Decimal("0.001")))

    def to_grams(self) -> int:
        return int(self.kg.scaleb(3))

    def __lt__(self, other: Weight) -> bool:
        return self.kg < other.kg

    def __le__(self, other: Weight) -> bool:
        return self.kg <= other.kg

    def __gt__(self, other: Weight) -> bool:
        return self.kg > other.kg

    def __ge__(self, other: Weight) -> bool:
        return self.kg >= other.kg

    def __str__(self) -> str:
        return f"{self.kg} kg"


MAX_UNTRUSTED_CHARS = 20_000


class UntrustedText(BaseModel):
    """Text from outside the system (I5).

    It is data: never interpolated into instructions, never parsed for commands. The only
    way it reaches a prompt is inside a delimited block rendered by the prompt registry,
    through the `neutralise` filter. The type exists so that "is this text trusted?" is
    answered by the type system rather than by whoever is reading the code that day.
    """

    model_config = _FROZEN

    text: str
    origin: Literal[
        "seller_email",
        "seller_portal",
        "jira_comment",
        "shipment_note",
        "dispute_form",
        "image_check",
    ]
    source_ref: str
    truncated: bool = False
    content_hash: PayloadHash

    @classmethod
    def create(
        cls,
        text: str,
        origin: Literal[
            "seller_email",
            "seller_portal",
            "jira_comment",
            "shipment_note",
            "dispute_form",
            "image_check",
        ],
        source_ref: str,
    ) -> Self:
        """Build one, truncating at the cap and recording that it was truncated.

        The caller never sets `truncated` itself: a reader downstream must be able to trust
        that the flag means what it says (the interpreter is told when it did not see the
        whole reply).
        """
        truncated = len(text) > MAX_UNTRUSTED_CHARS
        body = text[:MAX_UNTRUSTED_CHARS] if truncated else text
        return cls(
            text=body,
            origin=origin,
            source_ref=source_ref,
            truncated=truncated,
            content_hash=payload_hash(body),
        )
