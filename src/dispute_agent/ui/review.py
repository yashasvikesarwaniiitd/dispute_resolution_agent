"""Screen 3: the reviewer's queue, with the AI's recommendation beside each item (ADR 0044).

The decision itself stays in `ReviewConsole` (P3-06): the reviewer's identity comes from
sign-in, an approval binds to the stored text's hash, and a rejection needs a reason. This
adds only what the reviewer needs to judge well - what the investigator proposed and why,
and the case it belongs to - next to exactly what will happen if they say yes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActionStatus, ActionType, CaseState
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.ui.seller import Ticket, ticket
from dispute_agent.ui.trail import Recommendation, recommendation
from dispute_agent.ui.trail_words import action_words

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from dispute_agent.infra.sqlite.unit_of_work import SqliteUnitOfWork
    from dispute_agent.kernel.domain.ids import ActionId, CaseId
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
        item = console.view(
            approval, intents=work.intents, assessments=work.assessments, actions=work.actions
        )
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


#: How long a card stays hidden after a reviewer acts, if the case has not changed by then.
HOLD_S = 60.0


def not_yet_applied(
    people: Sequence[Ticket],
    acted: Mapping[str, str],
    *,
    acted_at: Mapping[str, float] | None = None,
    now: float | None = None,
) -> list[Ticket]:
    """The cases to show, leaving out one a reviewer acted on until the agent has moved it.

    `acted` maps a case id to its `updated_at` (ISO) when the reviewer acted. The card is
    hidden only while the case is unchanged, so the same choice is not offered twice in the
    seconds before the agent applies it. Once the case changes - a Return comes back to a
    person after its new round, say - it is a new question and shows again.

    A decision the agent refuses leaves the case unchanged (the refusal is a line on the
    trail), which hid the case for the rest of the session: "With a person (0)" for a case
    that was with a person (live demo, 2026-11-22). With `acted_at` (epoch seconds) and
    `now`, the card comes back after HOLD_S whatever happened.
    """

    def hidden(p: Ticket) -> bool:
        if acted.get(p.case_id) != p.updated_at.isoformat():
            return False
        if acted_at is None or now is None or p.case_id not in acted_at:
            return True
        return now - acted_at[p.case_id] < HOLD_S

    return [p for p in people if not hidden(p)]


@dataclass(frozen=True)
class Unknown:
    """An action the agent could not confirm, which a person must answer for (T37)."""

    action_id: ActionId
    #: "the explanation email", "the credit of ₹1,820.00": what to look for.
    what: str
    #: Where to look: "the test Gmail's Sent folder", "the ledger".
    where: str


WHERE = {
    ActionType.SEND_MESSAGE: "the Sent folder of the agent's mailbox",
    ActionType.POST_CREDIT: "the ledger",
}


def unknown_actions(work: SqliteUnitOfWork, case_id: CaseId) -> list[Unknown]:
    """Every action on the case whose fate is unknown, in the order they were planned."""
    found = []
    for action in work.actions.for_case(case_id):
        if action.status is not ActionStatus.UNKNOWN:
            continue
        payload = work.intents.get(action.intent_id).payload if action.intent_id else None
        _verb, what = action_words(payload)
        amount = getattr(payload, "amount", None)
        if amount is not None:
            what = f"{what} of {render_fact(amount)}"
        found.append(Unknown(action.id, what, WHERE.get(action.action_type, "the far end")))
    return found


__all__ = [
    "HOLD_S",
    "Card",
    "Unknown",
    "not_yet_applied",
    "queue",
    "unknown_actions",
    "with_a_person",
]
