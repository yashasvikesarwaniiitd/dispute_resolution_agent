"""The AssessmentStore port (kernel-interfaces.md section 6.2).

One assessment per gather round, keyed by it. `latest` is what a message cites its numbers
from: R09 refuses an outbound message whose figures do not trace to an assessment fact, so
this is the record that makes a number in an email defensible.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.evidence import ChargeAssessment
from dispute_agent.kernel.domain.ids import AssessmentId, CaseId


class AssessmentStore(Protocol):
    def add(self, assessment: ChargeAssessment) -> None: ...
    def latest(self, case_id: CaseId) -> ChargeAssessment | None: ...

    def get(self, assessment_id: AssessmentId) -> ChargeAssessment | None:
        """One assessment by id. The review page uses it so a number's source is taken from
        the assessment the message was drafted from, not whichever round came last."""
        ...
