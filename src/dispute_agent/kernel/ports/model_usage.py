"""The ModelUsageLedger port (ADR 0017): where the spending cap reads what was spent.

Three methods, all a budget needs. It records every call, success or not, because a call
that failed was usually still billed - a cap that counted only the good answers would let a
model that keeps answering badly spend without limit.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from dispute_agent.kernel.domain.ids import CaseId
from dispute_agent.kernel.domain.model_calls import ModelCallRecord


class ModelUsageLedger(Protocol):
    def record(self, call: ModelCallRecord) -> None:
        """Durable at once, independent of any case transaction (ADR 0030)."""
        ...

    def calls_for_case(self, case_id: CaseId) -> int:
        """Calls that reached a provider for this case. Budget refusals do not count."""
        ...

    def spend_usd(self, route: str) -> Decimal:
        """Everything this route has cost, ever. The cap is lifetime, like the credit."""
        ...
