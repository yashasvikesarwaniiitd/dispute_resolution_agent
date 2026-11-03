"""The AssessmentStore port (kernel-interfaces.md section 6.2).

One assessment per gather round, keyed by it. `latest` is what a message cites its numbers
from: R09 refuses an outbound message whose figures do not trace to an assessment fact, so
this is the record that makes a number in an email defensible.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.evidence import ChargeAssessment
from dispute_agent.kernel.domain.ids import CaseId


class AssessmentStore(Protocol):
    def add(self, assessment: ChargeAssessment) -> None: ...
    def latest(self, case_id: CaseId) -> ChargeAssessment | None: ...
