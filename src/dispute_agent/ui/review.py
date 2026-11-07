"""Screen 3: the reviewer's queue, with the AI's recommendation beside each item (ADR 0044).

The decision itself stays in `ReviewConsole` (P3-06): the reviewer's identity comes from
sign-in, an approval binds to the stored text's hash, and a rejection needs a reason. This
adds only what the reviewer needs to judge well - what the investigator proposed and why,
and the case it belongs to - next to exactly what will happen if they say yes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.ui.seller import Ticket, ticket
from dispute_agent.ui.trail import Recommendation, recommendation

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.modules.approvals.streamlit_ui.console import PendingItem, ReviewConsole


@dataclass(frozen=True)
class Card:
    item: PendingItem
    case: Ticket
    #: The proposal that led here; None when no proposal is recorded (a reminder, say).
    recommendation: Recommendation | None


def queue(work: SqliteUnitOfWork, console: ReviewConsole) -> list[Card]:
    """Everything awaiting a decision, oldest first, each with its case and recommendation."""
    cards = []
    for approval in console.queue():
        item = console.view(approval, intents=work.intents, assessments=work.assessments)
        case = work.cases.get(approval.request.case_id)
        proposals = list(work.proposals.for_case(case.id))
        cards.append(
            Card(
                item=item,
                case=ticket(case),
                recommendation=recommendation(proposals[-1]) if proposals else None,
            )
        )
    return cards


def with_a_person(work: SqliteUnitOfWork) -> Sequence[Ticket]:
    """Cases the agent handed to a person, oldest first: the reason is on each."""
    cases = work.cases.list_in_states([CaseState.NEEDS_HUMAN_REVIEW])
    return [ticket(c) for c in sorted(cases, key=lambda c: c.updated_at)]


__all__ = ["Card", "queue", "with_a_person"]
