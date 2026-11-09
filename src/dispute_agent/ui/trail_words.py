"""How each kind of step is said (design handoff 4.2, "copy fixes in the timeline").

Pure functions over what the case recorded. Each returns the words a reader needs on the
step's line, and puts what only an investigator needs - transaction ids, message ids, rule
codes, the facts' own labels - into the step's Details. No figure is computed here: amounts
are rendered from stored facts and records (I9 for the screen).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.actions import CreditPayload, IssuePayload, MessagePayload
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.policy.numbers import render_fact
from dispute_agent.ui.labels import EVIDENCE, MESSAGE, clock, words
from dispute_agent.ui.photo_check import IMAGE_CHECK as PHOTO_CHECK

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.actions import Action, ActionPayload, Approval
    from dispute_agent.kernel.domain.evidence import ChargeAssessment, EvidenceBundle
    from dispute_agent.kernel.policy.config import PolicyConfig

#: Evidence the first look does not ask for: the hub's finding comes after a re-measure, the
#: seller's reply after a request. Missing in a round that did not ask, it is "not needed".
LATER = frozenset({"ops.finding", "seller_reply"})


def evidence(bundle: EvidenceBundle, *, asked_later: bool) -> tuple[str, tuple[str, ...]]:
    """'Found 5 of 8. Not needed this round: hub finding. Not on file: hub's photo note.'"""
    found, later, absent, failed = [], [], [], []
    items = [i for i in bundle.items if i.result.requirement_key != PHOTO_CHECK]
    for item in items:
        key = item.result.requirement_key
        name = EVIDENCE.get(key, key).lower()
        status = item.result.status.value.lower()
        if status == "found":
            found.append(name)
        elif status == "failed":
            failed.append(name)
        elif key in LATER and not asked_later:
            later.append(name)
        else:
            absent.append(name)
    summary = f"Found {len(found)} of {len(items)}."
    if later:
        summary += " Not needed this round: " + ", ".join(later) + "."
    if absent:
        summary += " Not on file: " + ", ".join(absent) + "."
    if failed:
        summary += " Could not fetch: " + ", ".join(failed) + "."
    return summary, (("Found: " + ", ".join(found) + ".") if found else "No item was found.",)


def recalculation(
    found: ChargeAssessment | None, *, closed_ineligible: bool
) -> tuple[str, tuple[str, ...]]:
    """What the recalculation showed, in code's own figures; the facts go in Details."""
    if found is None:
        return "", ()
    facts = {fact.fact_id: render_fact(fact.value) for fact in found.facts}
    details = tuple(f"{fact.label.capitalize()}: {render_fact(fact.value)}" for fact in found.facts)
    charged, correct, over = (
        facts.get(k) for k in ("f_charged", "f_recalculated", "f_discrepancy")
    )
    if closed_ineligible:
        return (
            "Worked out before the scan history was read. Not used, because the shipment turned "
            "out not to be eligible.",
            details,
        )
    errors = found.definite_errors
    if "duplicate_charge" in errors and found.duplicate_debit_total is not None:
        dup = render_fact(found.duplicate_debit_total)
        return (
            f"The weight charge of {charged} is correct, but it was billed twice. Code proved a "
            f"duplicate charge of {dup}.",
            details,
        )
    figures = (
        f"Charged {charged}, correct {correct}, overcharged {over}. "
        if charged and correct and over
        else ""
    )
    proved = (
        "Code proved " + ", ".join(f"a {words(e)}" for e in errors) + "."
        if errors
        else "Code found no definite error."
    )
    return figures + proved, details


def approved(approval: Approval, approver: str) -> str:
    """'owner approved the credit of ₹1,820.00' / '… approved the evidence request email'."""
    request = approval.request
    if request.amount is not None:
        what = f"the credit of {render_fact(request.amount)}"
    elif request.message_kind is not None:
        what = MESSAGE.get(request.message_kind, "the email")
    else:
        what = words(request.subject.value.lower())
    return f"{approver or 'A person'} {approval.decision.value.lower()} {what}"


def executed(action: Action, payload: ActionPayload | None) -> tuple[str, str, tuple[str, ...]]:
    """The title, the line and the Details of an action the agent took."""
    ref = action.external_ref or ""
    if isinstance(payload, CreditPayload):
        return (
            "Posted a credit to the ledger",
            f"{render_fact(payload.amount)} for {words(payload.reason_code)}.",
            (
                f"Transaction {ref}" if ref else "Transaction not recorded",
                "Posted once, with an idempotency key",
            ),
        )
    if isinstance(payload, MessagePayload):
        what = MESSAGE.get(payload.kind, "the email")
        return f"Sent {what}", f"To {payload.to}.", ((f"Message {ref}",) if ref else ())
    if isinstance(payload, IssuePayload):
        return (
            "Opened a ticket for the hub",
            f"Jira {ref}: a re-measure of the parcel." if ref else "A re-measure of the parcel.",
            ((f"Issue {ref}",) if ref else ()),
        )
    return "Took an action", "", ((ref,) if ref else ())


def proved(payload: ActionPayload | None, to_state: CaseState | None) -> str:
    """What the read-back proved, and where the case went next."""
    if isinstance(payload, CreditPayload):
        then = (
            " Next, an email to the seller needed approval."
            if to_state is CaseState.AWAITING_APPROVAL
            else ""
        )
        return "The credit is in the ledger." + then
    if isinstance(payload, IssuePayload):
        return "The hub's ticket was read back. Now waiting for the hub."
    ending = {
        CaseState.RESOLVED_CREDITED: " The case is resolved: credited.",
        CaseState.RESOLVED_UPHELD: " The case is resolved: charge upheld.",
        CaseState.AWAITING_SELLER: " Now waiting for the seller.",
    }
    return (
        "The email was read back as sent." + ending.get(to_state, "")
        if to_state
        else "The email was read back as sent."
    )


def waiting(
    case_state: CaseState,
    pending: Approval | None,
    policy: PolicyConfig | None,
) -> str:
    """The 'Now: waiting for …' line that ends an open case's timeline."""
    if case_state is CaseState.AWAITING_APPROVAL and pending is not None:
        request = pending.request
        what = (
            f"the {render_fact(request.amount)} credit"
            if request.amount is not None
            else MESSAGE.get(request.message_kind, "the email")
            if request.message_kind
            else "a decision"
        )
        return f"Now: waiting for a reviewer to approve {what}. Requested {clock(request.requested_at)}."
    if case_state is CaseState.NEEDS_HUMAN_REVIEW:
        return "Now: waiting for a reviewer to take it over or return it to the agent."
    if case_state is CaseState.AWAITING_SELLER:
        timers = policy.timers if policy is not None else None
        hours = (
            f" A reminder needs approval after {timers.reminder_after_hours} h; the case closes at "
            f"{timers.decision_after_hours} h."
            if timers is not None
            else ""
        )
        return "Now: waiting for the seller's reply." + hours
    if case_state is CaseState.AWAITING_OPS:
        return "Now: waiting for the hub's finding."
    return "Now: the agent is working on it."


__all__ = ["LATER", "approved", "evidence", "executed", "proved", "recalculation", "waiting"]
