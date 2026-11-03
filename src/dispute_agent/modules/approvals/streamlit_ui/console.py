"""The review console's decision logic, with no Streamlit in it (P3-06, ADR 0018, I13).

Separate from the page on purpose: a decision that can only be exercised by driving a web
UI is a decision nobody tests properly, and this is the point where a person authorises the
system to write to the outside world.

**The reviewer's identity has no parameter.** `ReviewConsole` is constructed with the actor
the sign-in established, and `approve` and `reject` take an approval id and a note. There is
no argument an incoming request could use to claim a different reviewer, which is ADR 0018
made structural rather than remembered — the same shape as `prove()` having no parameter a
sentence could arrive through.

**What is approved is the hash, not the page.** The approval row already carries the hash of
the exact text; the console never re-renders or re-derives it. So a draft edited between
being shown and being approved cannot be approved, and R10 refuses to send any text whose
hash is not the approved one.

The console reads and decides. It does not send: approving sets a row, and the orchestrator
picks the case up from there. A console that could send would be a second path to the
outside world, bypassing the executor and everything it guarantees (I2).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import ActorKind, ApprovalDecision
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, DomainValidationError
from dispute_agent.kernel.domain.models import Actor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.domain.evidence import Fact
    from dispute_agent.kernel.domain.ids import ApprovalId
    from dispute_agent.kernel.ports import ApprovalStore, Clock

#: Every human actor id starts with this. R10 checks the same prefix before allowing a send,
#: so a decision recorded any other way cannot authorise one.
HUMAN_PREFIX = "human:"


def sign_in(provided: str | None, expected: str | None, reviewer_id: str) -> Actor:
    """Establish who is reviewing, for the life of the session (ADR 0018).

    Compared with `compare_digest`, so the time the check takes does not depend on how much
    of the token was right. The console runs locally, which makes a timing attack
    far-fetched - and writing the careless version here would teach the careless version to
    whoever copies this file next.
    """
    if not expected:
        raise ConfigError(
            "REVIEW_CONSOLE_TOKEN is not set; the review queue authorises real sends and "
            "does not run without it (see docs/SETUP_CHANNELS.md section 4)"
        )
    if not provided or not hmac.compare_digest(provided, expected):
        raise AuthPortError("the review console token is not correct")
    return Actor(kind=ActorKind.HUMAN, id=f"{HUMAN_PREFIX}{reviewer_id}")


@dataclass(frozen=True)
class PendingItem:
    """One row of the queue: the draft, and where every number in it came from.

    `numbers` is the part that makes a review meaningful. "Approve this message" is a
    question nobody can answer well; "approve this message, in which ₹152.00 is the charge
    from the ledger and 1,980 g is the chargeable weight from the hub measurement" is.
    """

    approval: Approval
    subject: str
    body: str
    numbers: tuple[tuple[str, str], ...] = ()

    @property
    def case_id(self) -> str:
        return str(self.approval.request.case_id)

    @property
    def kind(self) -> str:
        kind = self.approval.request.message_kind
        return kind.value if kind else "unknown"


@dataclass
class ReviewConsole:
    """Reads the queue and records decisions. Never sends anything.

    `reviewer` is set once, by `sign_in`. Note that no method below takes an actor: there
    is nothing for a request body to override.
    """

    store: ApprovalStore
    clock: Clock
    reviewer: Actor

    def __post_init__(self) -> None:
        if self.reviewer.kind is not ActorKind.HUMAN:
            raise DomainValidationError(
                "the review console records human decisions; a system actor cannot approve "
                "a message (ADR 0018)"
            )
        if not self.reviewer.id.startswith(HUMAN_PREFIX):
            raise DomainValidationError(
                f"a reviewer id must start with {HUMAN_PREFIX!r}, got {self.reviewer.id!r}"
            )

    # -- reading -------------------------------------------------------------------------

    def queue(self) -> Sequence[Approval]:
        """Everything awaiting a decision, oldest first."""
        return self.store.pending()

    def item(
        self, approval: Approval, subject: str, body: str, facts: Sequence[Fact]
    ) -> PendingItem:
        """One queue row, with each number paired with the fact it came from.

        The text is passed in by the caller, which reads it from the stored intent. The
        console never renders a message itself - a page that re-rendered the draft could
        show something subtly different from the text whose hash is about to be approved.
        """
        from dispute_agent.kernel.policy.numbers import render_fact

        numbers = tuple((render_fact(fact.value), fact.label) for fact in facts)
        return PendingItem(approval=approval, subject=subject, body=body, numbers=numbers)

    # -- deciding ------------------------------------------------------------------------

    def approve(self, approval_id: ApprovalId, note: str = "") -> Approval:
        """Record a yes against the hash already on the approval.

        Nothing is sent here. The orchestrator picks the case up, the executor re-checks
        the binding, and R10 refuses any text whose hash is not this one.
        """
        return self.store.decide(
            approval_id, ApprovalDecision.APPROVED, self.reviewer, self.clock.now(), note
        )

    def reject(self, approval_id: ApprovalId, reason: str) -> Approval:
        """Record a no, with a reason.

        The reason is required because it is not bookkeeping: it returns to the case as
        `prior_rejections` and is shown to whatever drafts the next attempt, so "no" with
        no reason produces the same message again.
        """
        if not reason.strip():
            raise DomainValidationError(
                "a rejection needs a reason; it is shown to whatever drafts the next "
                "attempt, and without it the same message comes back"
            )
        return self.store.decide(
            approval_id, ApprovalDecision.REJECTED, self.reviewer, self.clock.now(), reason
        )


__all__ = ["HUMAN_PREFIX", "PendingItem", "ReviewConsole", "sign_in"]
