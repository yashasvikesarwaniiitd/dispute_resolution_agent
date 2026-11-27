"""The `search_past_disputes` investigation tool (ADR 0071).

Earlier disputes that share something with this case - the same seller, scanner, hub or
product - and how each ended. Read-only, over the kernel's `cases` table.

Another seller's disputes are only **counted** by outcome, never named (ADR 0058): the agent
learns "five other disputes on this scanner were credited", not whose they were - and only
when at least MIN_CASES disputes from at least MIN_SELLERS sellers stand behind the counts
(ADR 0072); below that, "insufficient_data". The query reads through `CaseOutcomes`, so the
case store behind it can change without the tool changing.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.tools import ToolOutput, ToolSpec

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.tools import ToolContext
    from dispute_agent.modules.cross_case.sql_context import ShipmentKeys
    from dispute_agent.modules.cross_case.sql_context.repository import CaseRow

#: How far back the tool looks, in cases, newest first.
SCAN = 500
MAX_LISTED = 10
#: Other sellers' outcomes are shown only above both (the same thresholds as scanner statistics).
MIN_CASES = 5
MIN_SELLERS = 3


class CaseOutcomes(Protocol):
    def recent_outcomes(self, limit: int) -> list[tuple[CaseRow, str, str | None]]:
        """(case, state, review_code), newest first."""
        ...


class PastDisputesArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: Literal["seller", "scanner", "hub", "sku"] = Field(
        default="seller", description="What an earlier dispute must share with this case."
    )
    limit: int = Field(default=5, ge=1, le=MAX_LISTED, description="Most recent first.")


class PastDisputesTool:
    """Implements InvestigationTool."""

    spec = ToolSpec(
        name="search_past_disputes",
        description=(
            "Earlier disputes that share this case's seller, scanner, hub or product (SKU), and "
            "how each ended (state and review reason). This seller's own are listed; other "
            "sellers' are counted by outcome only. Use it to see whether this kind of dispute "
            "keeps happening and how it was settled."
        ),
        input_schema=PastDisputesArgs.model_json_schema(),
    )

    def __init__(self, repository: CaseOutcomes, keys: ShipmentKeys) -> None:
        self._repository = repository
        self._keys = keys

    def _keys_of(self, shipment_ref: str, seller_ref: str) -> dict[str, str]:
        try:
            found = dict(self._keys(shipment_ref))
        except LookupError:
            found = {}  # a shipment the evidence source does not know matches on seller only
        return found | {"seller": seller_ref}

    def run(self, ctx: ToolContext, args: Mapping[str, Any]) -> ToolOutput:
        asked = PastDisputesArgs.model_validate(dict(args))
        facts = ctx.facts
        wanted = self._keys_of(facts.shipment_ref, facts.seller_ref).get(asked.match)
        if wanted is None:
            return ToolOutput(tool=self.spec.name, ok=False, error=f"no_{asked.match}_known")
        own: list[dict[str, Any]] = []
        others: Counter[str] = Counter()
        other_sellers: set[str] = set()
        for row, state, review_code in self._repository.recent_outcomes(SCAN):
            if row.claim_ref == facts.claim_ref or row.opened_at >= facts.raised_at:
                continue  # never this dispute itself, nor one opened after it
            if self._keys_of(row.shipment_ref, row.seller_ref).get(asked.match) != wanted:
                continue
            if row.seller_ref == facts.seller_ref:
                own.append(
                    {
                        "shipment_ref": row.shipment_ref,
                        "opened_on": row.opened_at.date().isoformat(),
                        "state": state,
                        "review_code": review_code,
                    }
                )
            else:
                others[state] += 1
                other_sellers.add(row.seller_ref)
        enough = sum(others.values()) >= MIN_CASES and len(other_sellers) >= MIN_SELLERS
        return ToolOutput(
            tool=self.spec.name,
            ok=True,
            data={
                "match": asked.match,
                "value": wanted,
                "this_seller": own[: asked.limit],
                "this_seller_total": len(own),
                "other_sellers_by_outcome": (
                    dict(sorted(others.items())) if enough else "insufficient_data"
                ),
            },
        )


__all__ = ["PastDisputesTool"]
