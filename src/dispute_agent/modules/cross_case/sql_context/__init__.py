"""SqlPatternContextProvider: HOOK A's real adapter (P7-02, D3, I7).

Implements the existing `PatternContextProvider` port; the port does not change. Given a new
case's facts, it returns the open patterns that share one of the case's keys - scanner, hub,
SKU or seller - at most five, newest first, summaries at most 400 characters
(agent-design.md section 1). Every pattern it returns becomes a caution floor in the kernel.

Three rules carry the safety argument:

- **Read-only.** It never writes. Which patterns a case could have seen is recorded later, by
  the observer, conservatively (`observer.py`).
- **No self-evidence.** A case is never counted towards a pattern shown to it: if the case
  being decided is itself a member, the pattern is returned only when the *other* members
  alone meet the threshold, and the case is left out of `related_case_ids`.
- **Failure is not "none".** Any storage or lookup failure raises TransientPortError, which the
  kernel turns into a `pattern_context_unavailable` caution signal. Returning () would say
  "no pattern applies" when the truth is "we do not know".
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.exc import SQLAlchemyError

from dispute_agent.kernel.domain.errors import TransientPortError
from dispute_agent.kernel.domain.ids import CaseId, PatternId
from dispute_agent.kernel.domain.patterns import MAX_PATTERN_SUMMARY_CHARS, PatternRecord
from dispute_agent.modules.cross_case.sql_context.repository import (
    PatternRepository,
    StoredPattern,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.case import CaseFacts

#: agent-design.md section 1: the investigator sees at most five patterns.
MAX_PATTERNS = 5

#: A shipment's cluster keys - scanner, hub, SKU - by shipment ref. Built in app.py, because
#: those attributes live in the evidence module and modules never import each other. Raises
#: on failure; the provider turns that into TransientPortError.
ShipmentKeys = Callable[[str], Mapping[str, str]]

#: The kernel's PatternRecord has three statuses. A cause that was fixed and is being watched
#: is still a confirmed cause, so FIXED_MONITORING is shown as CONFIRMED.
_KERNEL_STATUS = {
    "UNCONFIRMED": "UNCONFIRMED",
    "CONFIRMED": "CONFIRMED",
    "FIXED_MONITORING": "CONFIRMED",
}


@dataclass
class SqlPatternContextProvider:
    """Implements PatternContextProvider over the module's own tables."""

    repository: PatternRepository
    keys: ShipmentKeys
    min_related_cases: int = 3

    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        try:
            keys = dict(self.keys(facts.shipment_ref))
            keys.setdefault("seller", facts.seller_ref)
            stored = self.repository.matching(keys.items())
            this_case = self.repository.case_for_claim(facts.claim_ref)
            found: list[PatternRecord] = []
            for pattern in stored:
                record = self._record(pattern, this_case)
                if record is not None:
                    found.append(record)
                if len(found) == MAX_PATTERNS:
                    break
            return tuple(found)
        except TransientPortError:
            raise
        except (SQLAlchemyError, LookupError, ValueError, OSError) as failed:
            raise TransientPortError(
                f"pattern context unavailable: {type(failed).__name__}"
            ) from failed

    def _record(self, pattern: StoredPattern, this_case: str | None) -> PatternRecord | None:
        members = self.repository.linked(pattern.id, "member")
        others = [m for m in members if m != this_case]
        if this_case in members and len(others) < self.min_related_cases:
            return None  # without this case, the cluster is not there: no self-evidence
        return PatternRecord(
            id=PatternId(pattern.id),
            status=_KERNEL_STATUS[pattern.status],
            summary=pattern.summary[:MAX_PATTERN_SUMMARY_CHARS],
            matched_on={pattern.dimension: pattern.match_value},
            related_case_ids=tuple(CaseId(m) for m in others),
            first_seen_at=pattern.first_seen_at,
            last_seen_at=pattern.last_seen_at,
        )


__all__ = ["MAX_PATTERNS", "PatternRepository", "ShipmentKeys", "SqlPatternContextProvider"]
