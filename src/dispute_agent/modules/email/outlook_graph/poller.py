"""Reading the mailbox: routing, sender authentication and bounces (P3-07, ADR 0022, I5).

This is the door outside text comes through, so the rules are about what the system refuses
to conclude from it.

**A reply that cannot be matched goes to a person.** Not to the most likely case, not to the
only open case for that seller if there are two. `orchestrator.md` section 2 gives the order
— conversation id first, then exactly one open case matching both the sender and a shipment
reference in the subject — and "exactly one" is the whole of it. Guessing which dispute a
reply belongs to means answering a seller about the wrong parcel.

**Sender authentication can only add caution.** A `fail` raises a signal. A `pass` relaxes
nothing, and neither does `none` — which is the common case, because the Outlook.com header
format is undocumented and a sender can add a header of their own. The adapter reads only
the topmost `Authentication-Results`, and only when it matches the configured authserv-id if
one is set. Residual risk RR7, recorded rather than assumed away.

**Bounce detection is a heuristic and says so.** Graph does not document how to recognise an
NDR. A false negative means a seller timer runs down on a message that never arrived; a
false positive means a case escalates that did not need to. The first is worse, so the
checks are broad, and every match is routed by the failed message's own key rather than by
anything the bounce claims about itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from dispute_agent.kernel.domain.values import MAX_UNTRUSTED_CHARS, UntrustedText

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from dispute_agent.kernel.domain.external import InboundMessage
    from dispute_agent.kernel.domain.ids import CaseId

AuthResult = Literal["pass", "fail", "none"]
Origin = Literal["seller_email", "seller_portal", "jira_comment", "shipment_note", "dispute_form"]

#: integrations.md 1.5 names 20,000 characters, and `UntrustedText` already enforces exactly
#: that. Re-exported here so a reader of the poller can see the bound without chasing it, and
#: so the two can never drift apart silently.
MAX_TEXT = MAX_UNTRUSTED_CHARS

#: Local parts that mean the message came from a mail system rather than a person.
POSTMASTERS = frozenset({"postmaster", "mailer-daemon", "mailerdaemon"})

#: The MIME type an NDR carries when it carries one.
DELIVERY_REPORT = "multipart/report"

AUTHENTICATION_RESULTS = "authentication-results"


@dataclass(frozen=True)
class OpenCase:
    """What routing needs to know about a case that might own a reply."""

    case_id: CaseId
    seller_contact: str
    shipment_ref: str


@dataclass(frozen=True)
class Routed:
    """Where a message belongs, or why it does not belong anywhere."""

    case_id: CaseId | None
    reason: str

    @property
    def routed(self) -> bool:
        return self.case_id is not None


def read_auth_result(
    headers: Sequence[Mapping[str, str]], trusted_authserv_id: str | None = None
) -> AuthResult:
    """What the receiving mail system said about the sender.

    Only the **topmost** header is read. A sender can add their own `Authentication-Results`
    further down, and taking the friendliest one in the list is how a forged pass gets
    believed. When an authserv-id is configured, a header from anyone else is ignored
    entirely rather than read charitably.

    Returns `none` whenever it cannot tell, which is most of the time: the Outlook.com
    format is undocumented. `none` raises no signal and relaxes nothing (RR7).
    """
    topmost = next(
        (h for h in headers if (h.get("name") or "").lower() == AUTHENTICATION_RESULTS), None
    )
    if topmost is None:
        return "none"

    value = (topmost.get("value") or "").lower()
    if trusted_authserv_id and not value.startswith(trusted_authserv_id.lower()):
        # Someone else's stamp. Not evidence about this message.
        return "none"

    # Failure first: a header saying both is a header to be suspicious of.
    if re.search(r"\b(dmarc|compauth)\s*=\s*fail\b", value):
        return "fail"
    if re.search(r"\b(dmarc|compauth)\s*=\s*pass\b", value):
        return "pass"
    return "none"


def looks_like_a_bounce(
    from_addr: str, headers: Sequence[Mapping[str, str]], content_type: str = ""
) -> bool:
    """Best effort, and deliberately broad (risk K2).

    Missing a bounce leaves a seller timer running down on a message that never arrived.
    Calling an ordinary reply a bounce sends a case to a person who can see it is not one.
    The first failure is worse and quieter, so this errs towards catching them.
    """
    local = from_addr.split("@")[0].strip().lower()
    if local in POSTMASTERS:
        return True
    if DELIVERY_REPORT in content_type.lower():
        return True
    return any(
        DELIVERY_REPORT in (h.get("value") or "").lower()
        for h in headers
        if (h.get("name") or "").lower() == "content-type"
    )


def failed_message_key(headers: Sequence[Mapping[str, str]]) -> str | None:
    """The idempotency key of the message that bounced, from the returned headers.

    Routed by our own key rather than by anything the bounce says about itself. An NDR is
    generated by a system we do not control, and its subject line is a sentence.
    """
    from dispute_agent.modules.email.outlook_graph.channel import IDEMPOTENCY_HEADER

    for header in headers:
        if (header.get("name") or "").lower() == IDEMPOTENCY_HEADER:
            value = (header.get("value") or "").strip()
            return value or None
    return None


def route(
    message: InboundMessage,
    *,
    conversation_case: CaseId | None,
    open_cases: Sequence[OpenCase],
) -> Routed:
    """Where this reply belongs (orchestrator.md section 2).

    Conversation id first, because it is the only identifier neither party typed. Then
    exactly one open case matching both the sender and a shipment reference in the subject.
    Anything else is unroutable, and unroutable means a person looks at it.
    """
    if conversation_case is not None:
        return Routed(conversation_case, "conversation")

    sender = message.from_addr.strip().lower()
    subject = message.subject.text

    candidates = [
        case
        for case in open_cases
        if case.seller_contact.strip().lower() == sender and case.shipment_ref in subject
    ]
    if len(candidates) == 1:
        return Routed(candidates[0].case_id, "contact_and_shipment")
    if len(candidates) > 1:
        # Two open disputes, one reply, no way to tell. Picking either means answering a
        # seller about the wrong parcel.
        return Routed(None, f"ambiguous:{len(candidates)}_open_cases")

    by_sender = [c for c in open_cases if c.seller_contact.strip().lower() == sender]
    if by_sender:
        return Routed(None, "no_shipment_reference_in_subject")
    return Routed(None, "unknown_sender")


def as_untrusted(text: str, origin: Origin, source_ref: str) -> UntrustedText:
    """Outside text, labelled and bounded, from the moment it enters (I5).

    Delegates the truncation rather than doing it. `UntrustedText.create` caps at the same
    20,000 characters *and records that it happened*, and an earlier version of this
    function cut the text first - which meant `create` saw a short string, set
    `truncated=False`, and a reply that had been cut in half read downstream as a complete
    one. The cap is the kernel's to apply, because the flag is what makes it safe.
    """
    return UntrustedText.create(text, origin, source_ref)


__all__ = [
    "AUTHENTICATION_RESULTS",
    "DELIVERY_REPORT",
    "MAX_TEXT",
    "POSTMASTERS",
    "AuthResult",
    "OpenCase",
    "Routed",
    "as_untrusted",
    "failed_message_key",
    "looks_like_a_bounce",
    "read_auth_result",
    "route",
]
