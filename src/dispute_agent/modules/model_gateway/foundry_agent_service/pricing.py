"""Prices for a route's model, from `config/pricing.yaml` (config.md section 5).

The owner copies these from the Azure portal at provisioning (foundry.md section 10: the
public pricing page shows "$-" for gpt-5-mini, so the price is not something code can know).
A route with no price row is refused at start-up: a spending cap with no prices would count
every call as free, which is the one way a cap can be silently switched off.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import yaml
from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.errors import ConfigError

if TYPE_CHECKING:
    from pathlib import Path

MTOK = Decimal(1_000_000)


class Price(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    input_per_mtok: Decimal = Field(gt=0)
    output_per_mtok: Decimal = Field(gt=0)
    source: str = Field(min_length=1)

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        return (
            Decimal(input_tokens) * self.input_per_mtok
            + Decimal(output_tokens) * self.output_per_mtok
        ) / MTOK


def load_price(path: Path, route: str, model: str) -> Price:
    if not path.is_file():
        raise ConfigError(f"no pricing file at {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    row = ((raw.get("routes") or {}).get(route) or {}).get(model)
    if not row:
        raise ConfigError(
            f"no price for {model!r} on route {route!r} in {path.name}: copy it from the "
            "Azure portal (docs/SETUP_FOUNDRY.md, step 'Prices'). Spending caps cannot work "
            "without one."
        )
    try:
        return Price.model_validate(row)
    except ValueError as bad:
        raise ConfigError(f"the price row for {model!r} is incomplete: {bad}") from None


__all__ = ["Price", "load_price"]
