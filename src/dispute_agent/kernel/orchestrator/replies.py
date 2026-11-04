"""The reply round: a seller's reply becomes evidence, and the contact counters become real.

T26 sends a case back to GATHERING_EVIDENCE when a reply arrives. There, `ReplyReader` takes
every reply not yet interpreted and turns each into one evidence item:

- the reply text itself, as `UntrustedText` (I5) - screened by the input screen like every
  other outside text, delimited in every prompt, never merged into an instruction;
- the interpreter's `ReplyReading` as the item's value - labelled input that can only add
  caution (R20, R21), never set an amount or approve anything.

If the interpreter is missing or fails, the item is FAILED (`reply_unreadable`), and the
baseline and policy send the case to a person (agent-design.md: "a reply exists that could
not be read"). A reply is never silently skipped, and never read twice: the message row
records the round that interpreted it.

`contact_status` gives the investigate step and the policy engine the same counters
(agent-design.md section 1): whether a reply is in this round, how many clarifications went
out, and whether operations is still remeasuring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    EvidenceStatus,
    MessageKind,
    ProposalAction,
    Provenance,
    TimerKind,
)
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.evidence import EvidenceItem, EvidenceResult
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import EvidenceItemId
from dispute_agent.kernel.domain.inbound import SellerReplyReceived, parse_inbound
from dispute_agent.kernel.domain.reasoning import ContactStatus, ReplyContext, ReplyReading

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.actions import Action, ActionIntent, Timer
    from dispute_agent.kernel.domain.evidence import EvidenceBundle
    from dispute_agent.kernel.domain.ids import CaseId, InboxId, IntentId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal
    from dispute_agent.kernel.ports import IdGenerator, ReplyInterpreter

#: The requirement key every reply item carries. Not in the pack's evidence plan: a reply is
#: not something we fetch, it is something that arrived.
SELLER_REPLY = "seller_reply"
#: A reply whose sender failed SPF/DKIM/DMARC (T03). Its own key, so readings ignore it.
SENDER_AUTH = "seller_reply_sender_auth"
SOURCE = "seller_email"


class Messages(Protocol):
    def uninterpreted(self, case_id: CaseId) -> Sequence[object]: ...
    def mark_interpreted(self, message_id: str, gather_round: int) -> None: ...


class Inbox(Protocol):
    def get(self, inbox_id: InboxId) -> dict[str, object]: ...


class Evidence(Protocol):
    def add(self, item: EvidenceItem) -> None: ...


class Proposals(Protocol):
    def for_case(self, case_id: CaseId) -> Sequence[Proposal]: ...


@dataclass
class ReplyReader:
    messages: Messages
    inbox: Inbox
    evidence: Evidence
    proposals: Proposals
    ids: IdGenerator
    interpreter: ReplyInterpreter | None = None

    def read(self, case: Case, now: datetime) -> int:
        """Interpret every new reply into this round. Returns how many were read."""
        count = 0
        for message in self.messages.uninterpreted(case.id):
            reply = parse_inbound(str(self.inbox.get(message.inbox_id)["event_json"])).payload  # type: ignore[attr-defined]
            if not isinstance(reply, SellerReplyReceived):
                continue
            self.evidence.add(self._item(case, reply, now))
            if reply.sender_auth == "fail":
                self.evidence.add(self._auth_failed(case, reply, now))
            self.messages.mark_interpreted(message.id, case.gather_round)  # type: ignore[attr-defined]
            count += 1
        return count

    def _item(self, case: Case, reply: SellerReplyReceived, now: datetime) -> EvidenceItem:
        reading, error = self._interpret(case, reply)
        result = EvidenceResult(
            requirement_key=SELLER_REPLY,
            source=SOURCE,
            status=EvidenceStatus.FOUND if reading else EvidenceStatus.FAILED,
            value=reading.model_dump(mode="json") if reading else None,
            untrusted=(reply.body,),
            error_code=error,
            retrieved_at=now,
        )
        return EvidenceItem(
            id=EvidenceItemId(self.ids.new_id("evi")),
            case_id=case.id,
            gather_round=case.gather_round,
            provenance=Provenance.SELLER,
            result=result,
            content_hash=payload_hash(result.model_dump(mode="json")),
        )

    def _auth_failed(self, case: Case, reply: SellerReplyReceived, now: datetime) -> EvidenceItem:
        """The sender did not authenticate (T03): recorded, and read as caution."""
        result = EvidenceResult(
            requirement_key=SENDER_AUTH,
            source=SOURCE,
            status=EvidenceStatus.FOUND,
            value={"result": "fail", "message": reply.external_message_id},
            retrieved_at=now,
        )
        return EvidenceItem(
            id=EvidenceItemId(self.ids.new_id("evi")),
            case_id=case.id,
            gather_round=case.gather_round,
            provenance=Provenance.SYSTEM,
            result=result,
            content_hash=payload_hash(result.model_dump(mode="json")),
        )

    def _interpret(
        self, case: Case, reply: SellerReplyReceived
    ) -> tuple[ReplyReading | None, str | None]:
        if self.interpreter is None:
            return None, "reply_unreadable:no_interpreter"
        try:
            return (
                self.interpreter.interpret(
                    ReplyContext(
                        case_id=case.id,
                        case_ref=str(case.id),
                        reply=reply.body,
                        requested_items=requested_items(self.proposals.for_case(case.id)),
                        facts=case.facts,
                        reply_truncated=reply.body.truncated,
                    )
                ),
                None,
            )
        except KernelError as failed:
            # Any model failure, budget stop or guardrail block: unread, and said so - with
            # the budget's own kind, so the reviewer sees which limit was reached.
            cause = getattr(failed, "kind", None) or failed.code
            return None, f"reply_unreadable:{cause}"


def requested_items(proposals: Sequence[Proposal]) -> tuple[str, ...]:
    """What the latest evidence request asked for (the reminder and the interpreter both)."""
    for proposal in reversed(list(proposals)):
        if proposal.action is ProposalAction.REQUEST_SELLER_EVIDENCE:
            return proposal.action_parameters.requested_items
    return ()


def readings_in(bundle: EvidenceBundle) -> tuple[ReplyReading, ...]:
    """The readings of the replies in this round, for R20/R21 and the briefing."""
    found: list[ReplyReading] = []
    for item in bundle.items:
        result = item.result
        if result.requirement_key != SELLER_REPLY or result.value is None:
            continue
        try:
            found.append(ReplyReading.model_validate(dict(result.value), strict=False))
        except ValidationError:
            continue  # stored by us; a bad row is a bug, and R21/B2 still see the FAILED ones
    return tuple(found)


def sender_unauthenticated(bundle: EvidenceBundle) -> bool:
    return any(item.result.requirement_key == SENDER_AUTH for item in bundle.items)


def reply_in(bundle: EvidenceBundle) -> bool:
    return any(item.result.requirement_key == SELLER_REPLY for item in bundle.items)


class Actions(Protocol):
    def for_case(self, case_id: CaseId) -> Sequence[Action]: ...


class Intents(Protocol):
    def get(self, intent_id: IntentId) -> ActionIntent: ...


class Timers(Protocol):
    def active_for_case(self, case_id: CaseId) -> Sequence[Timer]: ...


def clarifications_sent(case: Case, actions: Actions, intents: Intents) -> int:
    """Clarification requests that went out (SENT or VERIFIED) for this case (R12)."""
    count = 0
    for action in actions.for_case(case.id):
        if action.action_type is not ActionType.SEND_MESSAGE:
            continue
        if action.status not in (ActionStatus.SENT, ActionStatus.VERIFIED):
            continue
        kind = getattr(intents.get(action.intent_id).payload, "kind", None)
        if kind is MessageKind.CLARIFICATION_REQUEST:
            count += 1
    return count


def ops_outstanding(case: Case, timers: Timers) -> bool:
    """OPS_TIMEOUT is active: operations is still remeasuring (T09, R19)."""
    return any(t.kind is TimerKind.OPS_TIMEOUT for t in timers.active_for_case(case.id))


def contact_status(
    case: Case, bundle: EvidenceBundle, actions: Actions, intents: Intents, timers: Timers
) -> ContactStatus:
    return ContactStatus(
        reply_in_round=reply_in(bundle),
        clarifications_sent=clarifications_sent(case, actions, intents),
        remeasurement_outstanding=ops_outstanding(case, timers),
    )


__all__ = [
    "SELLER_REPLY",
    "ReplyReader",
    "contact_status",
    "readings_in",
    "reply_in",
    "requested_items",
]
