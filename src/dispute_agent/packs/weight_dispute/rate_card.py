"""The rate card: every figure the calculator uses (money-rules.md, D2, D10).

**Not one rate, slab, zone, threshold or divisor appears in the calculator.** They are all
here, and all loaded from configuration. That is a project rule rather than a style
preference: this repository is independent and synthetic (D2), and a number hard-coded in
arithmetic is a number that looks like a claim about somebody's real tariff. Keeping them in
a file keeps them visibly illustrative.

The values are validated at startup and fail loudly. A zero divisor divides by zero on the
first dispute; a missing zone silently charges nothing. Neither should be discovered by a
seller.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.values import Money, Weight

_FROZEN = ConfigDict(frozen=True, extra="forbid")


class ZoneRate(BaseModel):
    """What one zone charges per slab, in whole paise.

    Paise, not rupees: `rate_per_slab * slab_count` is then an integer number of paise and
    there is no rounding in the normal path at all (money-rules.md section 7).
    """

    model_config = _FROZEN

    zone: str
    rate_paise_per_slab: int = Field(gt=0)

    @property
    def rate(self) -> Money:
        return Money.from_paise(self.rate_paise_per_slab)


class RateCard(BaseModel):
    """Zones, slabs and the volumetric divisor. Illustrative synthetic values (D2, D10)."""

    model_config = _FROZEN

    version: str
    #: Public convention the pack models; configurable because it varies by carrier (D10).
    volumetric_divisor: int = Field(gt=0)
    #: Weight is rounded **up** to the next slab. That is the published convention, so it is
    #: a rule rather than an accident of arithmetic (money-rules.md section 7).
    slab_grams: int = Field(gt=0)
    zones: tuple[ZoneRate, ...]
    #: Below this dead weight, volumetric is not applied at all. Default 0 means the greater
    #: of dead and volumetric always wins, which is what money-rules.md section 7 describes.
    volumetric_applies_above_grams: int = Field(default=0, ge=0)
    #: Packaging types exempt from volumetric entirely (for example a flyer bag).
    volumetric_exempt_packaging: frozenset[str] = frozenset()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.zones:
            raise ConfigError("rate_card.zones is empty; every shipment has a zone")
        names = [zone.zone for zone in self.zones]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ConfigError(f"rate_card.zones repeats {sorted(duplicates)}")
        return self

    def rate_for(self, zone: str) -> Money:
        """Raises rather than defaulting. A zone nobody priced is a bill nobody can justify."""
        for entry in self.zones:
            if entry.zone == zone:
                return entry.rate
        raise ConfigError(
            f"rate_card has no zone {zone!r}; known zones are {sorted(z.zone for z in self.zones)}"
        )

    def rate_paise_for(self, zone: str) -> int:
        return self.rate_for(zone).to_paise()

    @property
    def slab(self) -> Weight:
        return Weight.from_grams(self.slab_grams)

    def zone_names(self) -> tuple[str, ...]:
        return tuple(zone.zone for zone in self.zones)


#: The shipped illustrative card. Phase 2 reads it from `config/policy.yaml`; this is the
#: fallback and the thing the tests pin, so the kurta worked example has one home.
DEFAULT_RATE_CARD = RateCard(
    version="synthetic-2026-01",
    volumetric_divisor=5000,
    slab_grams=500,
    zones=(
        ZoneRate(zone="A", rate_paise_per_slab=3000),
        ZoneRate(zone="B", rate_paise_per_slab=3800),
        ZoneRate(zone="C", rate_paise_per_slab=4500),
        ZoneRate(zone="D", rate_paise_per_slab=5800),
        ZoneRate(zone="E", rate_paise_per_slab=7000),
    ),
)


def load_rate_card(raw: object) -> RateCard:
    """Parse and validate, naming the key that is wrong (the card's acceptance test)."""
    if raw is None:
        return DEFAULT_RATE_CARD
    if not isinstance(raw, dict):
        raise ConfigError(f"rate_card must be a mapping, not {type(raw).__name__}")
    try:
        return RateCard.model_validate(raw)
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"rate_card is not usable: {exc}") from exc


def decimal_cm(value: object, what: str) -> Decimal:
    """A dimension in centimetres, to at most one decimal place (millimetres).

    Floats are refused here rather than quietly accepted, because a dimension is the first
    input to every number this pack produces (I9).
    """
    from dispute_agent.kernel.domain.errors import DomainValidationError

    if isinstance(value, bool | float):
        raise DomainValidationError(f"{what} must not come from a float: {value!r}")
    try:
        dimension = Decimal(value) if not isinstance(value, Decimal) else value  # type: ignore[arg-type]
    except Exception as exc:
        raise DomainValidationError(f"{what} is not a number: {value!r}") from exc
    if not dimension.is_finite() or dimension <= 0:
        raise DomainValidationError(f"{what} must be a positive length: {value!r}")
    if -dimension.as_tuple().exponent > 1:  # type: ignore[operator]
        raise DomainValidationError(
            f"{what} is measured to the millimetre, so at most one decimal place: {value!r}"
        )
    return dimension
