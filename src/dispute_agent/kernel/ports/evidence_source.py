"""The EvidenceSource port (kernel-interfaces.md section 6.4).

Read-only, and it never invents. A source returns FOUND with a value, MISSING, or FAILED with
an error code — three different answers, kept different, because "we could not find out" and
"there is nothing there" lead to different decisions.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.evidence import EvidenceRequirement, EvidenceResult
from dispute_agent.kernel.domain.ids import CaseId


class EvidenceSource(Protocol):
    name: str

    def fetch(
        self, case_id: CaseId, facts: CaseFacts, requirement: EvidenceRequirement
    ) -> EvidenceResult:
        """Raises ModuleUnavailable only.

        Every other failure is a FAILED result, so a flaky source degrades the evidence
        rather than crashing the case. Timeout <= 10 s.
        """
        ...
