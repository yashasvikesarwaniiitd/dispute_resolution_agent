"""Intake: getting a dispute into the system exactly once (orchestrator.md 2, ADR 0006).

Three things happen here and each one is a guarantee rather than a convenience.

**Deduplication** is by `<source>:<source_event_id>`, settled by a UNIQUE index. A repeat
submission returns the first answer instead of opening a second case, so the dispute form can
be double-submitted and the mail poller can deliver twice without consequence.

**One open case per shipment and claim** is settled by a partial unique index. Two requests
racing both pass any check we could write in Python; the loser gets a ConcurrencyConflict,
re-runs routing, and finds the winner's case.

**The recipient allowlist** is I11, and it is checked before anything is written. This project
only ever writes to the owner's own test mailbox, so a dispute naming anyone else is refused
at the door — no inbox row, no case, no event (scenario S27). Refusing late would leave a
case that the system can never safely act on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.case import normalise_recipients
from dispute_agent.kernel.domain.enums import (
    ActorKind,
    CaseState,
    InboundEventType,
    InternalEventType,
)
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.events import Event, StepPayload
from dispute_agent.kernel.domain.models import Actor, Case, Cause

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime, timedelta

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.events import InboundEvent
    from dispute_agent.kernel.domain.ids import CaseId, CorrelationId, EventId
    from dispute_agent.kernel.ports import CaseRepository

SYSTEM = Actor(kind=ActorKind.SYSTEM, id="system")

#: Soft-terminal states can be reopened by the agent inside the window (transitions.yaml
#: `groups`); hard-terminal ones need a person.
SOFT_TERMINAL = (CaseState.RESOLVED_UPHELD, CaseState.CLOSED_NO_RESPONSE)


@dataclass(frozen=True)
class IntakeResult:
    """What intake decided, and why — so the caller does not have to infer it."""

    case: Case
    created: bool
    reopened_from: CaseId | None = None
    events: tuple[Event, ...] = ()


class RecipientNotAllowedError(DomainValidationError):
    """I11: this project writes only to the owner's own test addresses.

    A separate type because the API answers it with 422 and stores nothing, which is
    different from every other validation failure the orchestrator produces.
    """


def check_recipient(contact: str, allowed: Collection[str]) -> None:
    """Refuse before anything is written (scenario S27).

    An empty allowlist refuses everything rather than allowing everything. A misconfigured
    deployment that emails strangers is a worse failure than one that processes nothing, and
    "no list" is far more likely to be an oversight than an intention.
    """
    if contact.strip().lower() not in normalise_recipients(allowed):
        raise RecipientNotAllowedError(
            f"{contact} is not on the test recipient allowlist; this project writes only to "
            "the owner's own test addresses (I11)"
        )


class Intake:
    """Opens a case, or finds the one this dispute belongs to.

    Pure of I/O apart from the repositories handed to it: `open_or_get_case` takes a unit of
    work, so the whole decision — find, reopen or create — commits atomically with the events
    that record it.
    """

    def __init__(self, allowed_recipients: Collection[str], reopen_window: timedelta) -> None:
        self._allowed = tuple(allowed_recipients)
        self._reopen_window = reopen_window

    def check(self, facts: CaseFacts) -> None:
        check_recipient(facts.seller_contact, self._allowed)

    def open_or_get_case(
        self,
        cases: CaseRepository,
        event: InboundEvent,
        facts: CaseFacts,
        *,
        pack: str,
        now: datetime,
        case_id: CaseId,
        correlation_id: CorrelationId,
        opened_event_id: EventId,
        causation_id: EventId,
    ) -> IntakeResult:
        """Route a DISPUTE_RAISED to its case (orchestrator.md section 2).

        Three outcomes, in order: an open case for this pair absorbs it; a recently closed
        soft-terminal case is handed back for the matrix to reopen; otherwise a new case is
        opened, linked to whatever came before.
        """
        if event.type is not InboundEventType.DISPUTE_RAISED:
            raise DomainValidationError(
                f"intake opens cases from DISPUTE_RAISED, not {event.type.value}"
            )
        self.check(facts)

        open_case = cases.find_open(facts.shipment_ref, facts.claim_ref)
        if open_case is not None:
            return IntakeResult(case=open_case, created=False)

        latest = cases.find_latest(facts.shipment_ref, facts.claim_ref)
        if latest is not None and self._is_reopenable(latest, now):
            return IntakeResult(case=latest, created=False, reopened_from=latest.id)

        opened = Case(
            id=case_id,
            pack=pack,
            facts=facts,
            state=CaseState.RECEIVED,
            version=1,
            correlation_id=correlation_id,
            opened_at=now,
            updated_at=now,
            state_entered_at=now,
            prior_case_id=None if latest is None else latest.id,
        )
        opened_event = Event(
            id=opened_event_id,
            case_id=case_id,
            type=InternalEventType.CASE_OPENED,
            occurred_at=now,
            recorded_at=now,
            actor=SYSTEM,
            cause=_input_cause(causation_id),
            correlation_id=correlation_id,
            causation_id=causation_id,
            payload=StepPayload(step="intake", detail="case opened"),
        )
        return IntakeResult(case=opened, created=True, events=(opened_event,))

    def _is_reopenable(self, latest: Case, now: datetime) -> bool:
        """Soft-terminal, not superseded, and still inside the window.

        A hard-terminal case is never reopened by the agent: a credited dispute reopening
        itself is how one shipment gets paid twice.
        """
        if latest.closed_at is None:
            return False
        if latest.state not in SOFT_TERMINAL:
            return False
        if latest.superseded_by_case_id is not None:
            return False
        return now < latest.closed_at + self._reopen_window


def _input_cause(event_id: EventId) -> Cause:
    from dispute_agent.kernel.domain.enums import CauseKind as _CauseKind

    return Cause(kind=_CauseKind.INPUT_EVENT, ref=str(event_id))


__all__ = [
    "SOFT_TERMINAL",
    "Intake",
    "IntakeResult",
    "RecipientNotAllowedError",
    "check_recipient",
    "normalise_recipients",
]
