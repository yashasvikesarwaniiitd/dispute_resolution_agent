"""`make try-agent`: one synthetic dispute through the line, stopping at your approval (ADR 0029).

The prototype's front door, now a thin view over the full single-case run (`full_run.py`):
the same line, the same stations, the same rules - only it never answers an approval, so the
furthest a case reaches is the approval queue. One code path for the trial, the demo and the
end-to-end tests means they cannot drift apart.

**It cannot send anything.** It runs in a throwaway database, against the email and Jira
fakes and a temporary simulated ledger, and stops before any approval is given. The only
thing that leaves the machine is the model call, which goes through the spending guard and
is logged to `data/model_calls.sqlite3` like any other.

The recipient allowlist is the one synthetic test seller (`SELLER_CONTACT`), as the owner's
`.env` would name the test seller. A fixture with any other address is refused at intake,
exactly as the API refuses it (S27) - which is what `non_allowlisted_seller` is there to show.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.api.full_run import run_full
from dispute_agent.infra.clock import FakeClock
from dispute_agent.kernel.orchestrator.intake import RecipientNotAllowedError
from dispute_agent.kernel.policy.numbers import render_fact

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.reasoning import Proposal
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import Clock, IdGenerator, Reasoner


@dataclass(frozen=True)
class TrialReport:
    fixture: str
    #: None when intake refused the dispute; `refused` then says why.
    case: Case | None
    proposal: Proposal | None
    fell_back: bool
    email_subject: str | None
    email_body: str | None
    refused: str | None = None
    #: Set when the pending approval is a credit: the amount code derived, for the reviewer.
    credit_amount: str | None = None


def run_trial(
    name: str,
    reasoner: Reasoner,
    *,
    policy: PolicyConfig,
    clock: Clock,
    ids: IdGenerator,
    statement: str | None = None,
    notes: tuple[str, ...] | None = None,
) -> TrialReport:
    try:
        report = run_full(
            name,
            policy=policy,
            # The full run moves its own clock while it waits for read-backs; a trial stops at
            # the first approval, and must never move the caller's clock either way.
            clock=FakeClock(clock.now()),
            ids=ids,
            reasoner=reasoner,
            decide=lambda _approval: None,
            statement=statement,
            notes=notes,
        )
    except RecipientNotAllowedError as refused:
        return TrialReport(name, None, None, False, None, None, refused=str(refused))

    proposal = report.proposals[0] if report.proposals else None
    message: Any = next(
        (p for p in report.pending_payloads if getattr(p, "type", None) == "message"), None
    )
    credit: Any = next(
        (p for p in report.pending_payloads if getattr(p, "type", None) == "credit"), None
    )
    return TrialReport(
        fixture=name,
        case=report.case,
        proposal=proposal,
        fell_back=bool(proposal and proposal.fallback_used),
        email_subject=getattr(message, "subject", None),
        email_body=getattr(message, "body", None),
        credit_amount=render_fact(credit.amount) if credit is not None else None,
    )


__all__ = ["TrialReport", "run_trial"]
