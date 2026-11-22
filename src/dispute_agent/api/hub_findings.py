"""The hub's answer, from Jira into the case (card P3-07).

Two pieces, both outside the kernel:

- **HubFindingsJob** runs on the runner's tick. Every `every` it asks the ticketing system
  for findings newer than its last read (`fetch_findings`), finds the case whose own action
  opened that issue, keeps the answer for that case, and submits it as an OPS_FINDING_RECEIVED
  event. The dedupe key is the issue and comment, so an overlapping read delivers nothing
  twice. Dispatch routes it by the issue key, and the reaction table moves a case waiting on
  the hub into a new round.
- **answers_for** answers the evidence source's `ops.finding` in that round with the finding
  kept for the case, so the hub's figures are what code recalculates with. It reads memory,
  not the database: the round runs inside dispatch's write transaction, and a second
  connection there finds the database locked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select

from dispute_agent.infra.sqlite.tables import actions as actions_table
from dispute_agent.infra.sqlite.tables import cases as cases_table
from dispute_agent.kernel.domain.enums import ActionType, ActorKind, CaseState, InboundEventType
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.inbound import OpsFindingReceived
from dispute_agent.kernel.domain.models import Actor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from sqlalchemy import Engine

    from dispute_agent.kernel.domain.external import OpsFinding
    from dispute_agent.kernel.domain.ids import CaseId

log = structlog.get_logger(__name__)

JIRA = Actor(kind=ActorKind.EXTERNAL, id="jira")
#: How far back the first read looks, and how much each read overlaps the last. A comment
#: stamped in the instant between two reads is not lost; one read twice is a duplicate.
FIRST_LOOK_BACK = timedelta(days=7)
OVERLAP = timedelta(minutes=5)


def case_for_issue(engine: Engine) -> Callable[[str], str | None]:
    """The case whose own action opened this issue, or None for an issue it did not open."""

    def find(issue_key: str) -> str | None:
        with engine.connect() as conn:
            row = conn.execute(
                select(actions_table.c.case_id)
                .where(actions_table.c.action_type == ActionType.UPSERT_ISSUE.value)
                .where(actions_table.c.external_ref == issue_key)
            ).first()
        return None if row is None else str(row[0])

    return find


def waiting_on_hub(engine: Engine) -> Callable[[str], bool]:
    """Whether a case is waiting for the hub's answer right now."""

    def waiting(case_id: str) -> bool:
        with engine.connect() as conn:
            row = conn.execute(
                select(cases_table.c.state).where(cases_table.c.id == case_id)
            ).first()
        return row is not None and str(row[0]) == CaseState.AWAITING_OPS.value

    return waiting


@dataclass
class HubFindingsJob:
    fetch: Callable[[datetime], Sequence[OpsFinding]]
    submit: Callable[[InboundEvent], object]
    case_for: Callable[[str], str | None]
    #: A reply with no measurement is passed on only while its case waits on the hub: a
    #: "thanks" under a closed case's issue must not reopen it. None passes everything.
    waiting: Callable[[str], bool] | None = None
    #: case id -> the hub's latest answer on that case's issue; read by `answers_for`.
    answers: dict[str, OpsFinding] = field(default_factory=dict)
    every: timedelta = timedelta(seconds=60)
    name: str = "hub_findings"
    _last_run: datetime | None = field(default=None, repr=False)
    _since: datetime | None = field(default=None, repr=False)

    def due(self, now: datetime) -> bool:
        return self._last_run is None or now - self._last_run >= self.every

    def run(self, now: datetime) -> int:
        """Keep and submit every new finding on an issue a case opened; return how many."""
        self._last_run = now  # a failing read waits for the next interval, not the next tick
        since = (self._since or now - FIRST_LOOK_BACK) - OVERLAP
        delivered = 0
        for finding in self.fetch(since):
            case_id = self.case_for(finding.issue_key)
            if case_id is None:
                continue  # not an issue a case opened (a pattern investigation, say)
            unread = finding.kind == "inspected" and not finding.measured
            if unread and self.waiting is not None and not self.waiting(case_id):
                continue  # small talk after the answer, not a new answer
            kept = self.answers.get(case_id)
            if kept is None or finding.at >= kept.at:
                self.answers[case_id] = finding
            self.submit(
                InboundEvent(
                    type=InboundEventType.OPS_FINDING_RECEIVED,
                    source="jira",
                    source_event_id=f"{finding.issue_key}:{finding.finding_id}",
                    occurred_at=finding.at,
                    actor=JIRA,
                    payload=OpsFindingReceived(finding=finding),
                )
            )
            delivered += 1
            log.info("hub_finding_received", issue=finding.issue_key, kind=finding.kind)
        self._since = now
        return delivered


def answers_for(job: HubFindingsJob) -> Callable[[CaseId], OpsFinding | None]:
    """The evidence source's view of the job's answers."""
    return lambda case_id: job.answers.get(str(case_id))


__all__ = ["HubFindingsJob", "answers_for", "case_for_issue", "waiting_on_hub"]
