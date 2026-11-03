"""The evidence module, switched off (I6).

Every method raises. The temptation with a disabled evidence source is to return MISSING and
let the case carry on, which reads as "we looked and there was nothing there" — a different
and much worse answer than "we never looked". Raising keeps the two apart; the gather step
turns this into a FAILED evidence item carrying the module name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import ModuleUnavailable

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import EvidenceRequirement, EvidenceResult
    from dispute_agent.kernel.domain.ids import CaseId

MODULE = "evidence"


class DisabledEvidenceSource:
    """Implements EvidenceSource."""

    name = MODULE

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        raise ModuleUnavailable(MODULE)
