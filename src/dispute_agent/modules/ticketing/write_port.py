"""Presenting a `TicketingSystem` as the executor's `WritePort` (P3-04).

The same joining layer as the email one, and the same single transformation: the description
comes back as normalised plain text, because Jira may restructure the stored document and
the executor compares against the text in the stored intent, never against ADF.

`idempotent_writes` is read from the adapter rather than hard-coded here. It is False for
Jira and the executor reads it to decide whether a retry is safe, so a bridge that answered
on the adapter's behalf would be the worst possible place to get it wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.external import IssueSpec
from dispute_agent.modules.ticketing.jira_cloud.ticketing import normalise

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import IssuePayload
    from dispute_agent.kernel.domain.external import IssueRef, IssueSnapshot
    from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
    from dispute_agent.kernel.ports import TicketingSystem


@dataclass(frozen=True)
class NormalisedIssue:
    """A snapshot whose description is in the form the comparison uses."""

    key: str
    project_key: str
    labels: tuple[str, ...]
    summary: str
    description_text: str
    status: str
    resolution: str | None
    idempotency_key: IdempotencyKey | None


@dataclass
class IssueWritePort:
    """Implements WritePort over any TicketingSystem, real or fake.

    `send` takes the intent's own `IssuePayload` and builds the issue here, with the
    project taken from this bridge's one `project_key`. Before, the create used the spec's
    project and the look-before-create search used the adapter's, so a mismatch made the
    search silently blind and produced the duplicate issue ADR 0020 exists to prevent
    (Phase 3 review, finding 8). One source now feeds both.
    """

    system: TicketingSystem
    case_id: CaseId
    project_key: str

    @property
    def idempotent_writes(self) -> bool:
        return self.system.idempotent_writes

    @property
    def lookup_supported(self) -> bool:
        return self.system.lookup_supported

    def send(self, payload: Any, key: IdempotencyKey) -> IssueRef:
        approved: IssuePayload = payload
        return self.system.upsert_issue(
            IssueSpec(
                project_key=self.project_key,
                summary=approved.summary,
                description=approved.description,
                labels=approved.labels,
                case_id=self.case_id,
            ),
            key,
        )

    def find_sent(self, key: IdempotencyKey) -> IssueRef | None:
        return self.system.find_issue(key)

    def read_back(self, receipt: Any) -> NormalisedIssue:
        snapshot: IssueSnapshot = self.system.get_issue(receipt)
        return NormalisedIssue(
            key=snapshot.key,
            project_key=snapshot.project_key,
            labels=snapshot.labels,
            summary=snapshot.summary,
            description_text=normalise(snapshot.description_text),
            status=snapshot.status,
            resolution=snapshot.resolution,
            idempotency_key=snapshot.idempotency_key,
        )


__all__ = ["IssueWritePort", "NormalisedIssue"]
