"""The case desk: what a signed-in reviewer can do with a case handed to a person (ADR 0046).

Four decisions, each a transition the engine guards:

- **Decide** (`HUMAN_DECIDED_CASE`, T49-T50, ADR 0050): the person chooses from code's menu -
  uphold the charge, credit code's proven amount, ask the hub to remeasure, ask the seller
  for evidence. Only the choice is recorded: code works out the amount and the pack drafts
  the message, and a credit or message still waits for an approval of its exact amount or
  text. A note saying why is required, like a return's reason.

- **Take over** (`HUMAN_TOOK_CASE`, T33-T36): the agent stops; the person owns the case.
  With `notify_seller`, a handed-to-a-person notice is drafted and goes through the review
  queue like any message (T34).
- **Return to the agent** (`HUMAN_RETURNED_CASE`, T37): the whole deterministic stage runs
  again from evidence. A reason is required and kept on the case's record (the inbox row
  and its audit event). It is *not* shown to the investigator today - its reviewer notes
  come from rejected approvals only (BACKLOG). A case whose credit is already verified
  cannot return (the transition's own guard; checked here first so the reviewer is told
  rather than ignored). Nor can one with an action whose fate is unknown until the person
  says whether it happened (T37 `unknown_actions_resolved`; also checked here first).
- **Confirm a withdrawal** (`HUMAN_CONFIRMED_WITHDRAWAL`, T38): only when the case is with a
  person *because* the seller wrote that they withdraw. The transition alone would accept it
  for any case; the desk does not offer it for one.

**Why the desk records into the inbox, not the API.** `POST /inbound` refuses people's
decisions (403): it cannot tell who is calling, and dispatch rule P3 trusts the declared
actor. The desk is the authenticated path, like `ReviewConsole` for approvals: the reviewer
comes from `sign_in`, no method takes an actor, and the runner applies the event on its
next tick through dispatch, P3 included.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.enums import ActorKind, CaseState, InboundEventType, ReviewCode
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.events import InboundEvent
from dispute_agent.kernel.domain.inbound import (
    HumanConfirmedWithdrawal,
    HumanDecidedCase,
    HumanReturnedCase,
    HumanTookCase,
    UnknownActionResolution,
)
from dispute_agent.modules.approvals.streamlit_ui.console import HUMAN_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.events import InboundPayload
    from dispute_agent.kernel.domain.ids import ActionId
    from dispute_agent.kernel.domain.models import Actor, Case
    from dispute_agent.kernel.ports import Clock

SOURCE = "ops_console"
#: A person's answer for an action whose fate the agent could not confirm (T37).
DONE = "confirmed_done"
NOT_DONE = "confirmed_not_done"
RESOLUTIONS = (DONE, NOT_DONE)


class Inbox(Protocol):
    def accept(self, event: InboundEvent, received_at: datetime) -> object: ...


@dataclass
class CaseDesk:
    """Records a reviewer's decision on a case with a person. Never moves a case itself."""

    inbox: Inbox
    clock: Clock
    reviewer: Actor

    def __post_init__(self) -> None:
        if self.reviewer.kind is not ActorKind.HUMAN or not self.reviewer.id.startswith(
            HUMAN_PREFIX
        ):
            raise DomainValidationError("the case desk records a signed-in person's decisions")

    def take_over(self, case: Case, *, notify_seller: bool, note: str = "") -> None:
        _with_a_person(case)
        self._record(
            InboundEventType.HUMAN_TOOK_CASE,
            case,
            HumanTookCase(notify_seller=notify_seller, note=note.strip()[:500]),
        )

    def return_to_agent(
        self,
        case: Case,
        reason: str,
        *,
        credit_verified: bool,
        unknown: Sequence[ActionId] = (),
        resolutions: Mapping[ActionId, str] | None = None,
    ) -> None:
        """Send the case back for a new round (T37).

        `unknown` is every action on the case whose fate is unknown; `resolutions` is the
        person's answer for each ("confirmed_done" or "confirmed_not_done"). T37 refuses a
        Return that leaves one unanswered, and the refusal is only a line on the trail, so
        it is checked here first, where the person can be told.
        """
        _with_a_person(case)
        if not reason.strip():
            raise DomainValidationError(
                "returning a case needs a reason: it is kept on the case's record"
            )
        if credit_verified:
            raise DomainValidationError(
                "this case already has a verified credit, so it cannot go back to the agent; "
                "take it over instead"
            )
        answered = dict(resolutions or {})
        if any(answered.get(a) not in RESOLUTIONS for a in unknown):
            raise DomainValidationError(
                "say whether each action the agent could not confirm actually happened, "
                "before returning the case"
            )
        self._record(
            InboundEventType.HUMAN_RETURNED_CASE,
            case,
            HumanReturnedCase(
                reason=reason.strip()[:500],
                unknown_action_resolutions=tuple(
                    UnknownActionResolution(
                        action_id=a,
                        resolution=answered[a],  # checked above: one of RESOLUTIONS
                        evidence_note=reason.strip()[:500],
                    )
                    for a in unknown
                ),
            ),
        )

    def decide(
        self,
        case: Case,
        action: str,
        note: str,
        *,
        reason_code: str | None = None,
        requested_items: tuple[str, ...] = (),
    ) -> None:
        """Record the person's verdict. Code acts on it on the runner's next tick (ADR 0050)."""
        _with_a_person(case)
        if not note.strip():
            raise DomainValidationError(
                "a decision needs a note saying why: it is kept on the case's record"
            )
        if action == "propose_credit" and not reason_code:
            raise DomainValidationError("a credit needs the code-proven error it is for")
        if action == "request_seller_evidence" and not requested_items:
            raise DomainValidationError("choose at least one item to ask the seller for")
        self._record(
            InboundEventType.HUMAN_DECIDED_CASE,
            case,
            HumanDecidedCase(
                action=action,
                reason_code=reason_code,
                requested_items=requested_items,
                note=note.strip()[:500],
            ),
        )

    def confirm_withdrawal(self, case: Case, note: str = "") -> None:
        _with_a_person(case)
        if not can_confirm_withdrawal(case):
            raise DomainValidationError(
                "only a case the seller asked to withdraw can be closed as withdrawn"
            )
        self._record(
            InboundEventType.HUMAN_CONFIRMED_WITHDRAWAL,
            case,
            HumanConfirmedWithdrawal(note=note.strip()[:500]),
        )

    def _record(self, kind: InboundEventType, case: Case, payload: InboundPayload) -> None:
        now = self.clock.now()
        self.inbox.accept(
            InboundEvent(
                type=kind,
                source=SOURCE,
                source_event_id=f"desk-{uuid.uuid4().hex}",
                occurred_at=now,
                actor=self.reviewer,
                case_id=case.id,
                correlation_id=case.correlation_id,
                payload=payload,
            ),
            now,
        )


def can_confirm_withdrawal(case: Case) -> bool:
    reason = case.review_reason
    return reason is not None and reason.code is ReviewCode.SELLER_WITHDRAWAL_CLAIMED


def _with_a_person(case: Case) -> None:
    if case.state is not CaseState.NEEDS_HUMAN_REVIEW:
        raise DomainValidationError("this case is no longer waiting for a person")


__all__ = ["DONE", "NOT_DONE", "RESOLUTIONS", "SOURCE", "CaseDesk", "can_confirm_withdrawal"]
