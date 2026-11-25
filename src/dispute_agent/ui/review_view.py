"""The parts of a Review card, as escaped markup (design handoff 4, Screen 3; ADR 0048).

Pure functions over what the queue already holds: the recommendation, the exact credit or the
exact email, and where every number came from. Each value is escaped; recorded text (the
reasoning, a finding, a review detail) is shown "exactly as recorded" and never rendered (I5).
What is shown is what is approved: the email body is the stored text, unaltered except that
each cited number is marked and indexed (I3, I9, I13).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.ui.labels import MESSAGE, RULE, handed_over, reason, words
from dispute_agent.ui.theme import escape, pairs, recorded

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.enums import ReviewCode
    from dispute_agent.modules.approvals.streamlit_ui.console import PendingItem
    from dispute_agent.ui.trail import Recommendation

TEST_ADDRESS = "(the test seller address)"
B3_PROVED = "B3: one definite error proved by code:"


def kind_title(item: PendingItem) -> str:
    """ "Credit" or "Message: evidence request"."""
    return "Credit" if item.is_credit else f"Message: {words(item.kind)}"


def approve_label(item: PendingItem) -> str:
    """The primary button names the exact thing being approved."""
    return (
        f"Approve the {item.credit_amount} credit"
        if item.is_credit
        else "Approve and send this email"
    )


def what(item: PendingItem) -> str:
    """'the ₹152.00 credit' / 'the evidence request email', for the line after a decision."""
    if item.is_credit:
        return f"the {item.credit_amount} credit"
    kind = item.approval.request.message_kind
    return MESSAGE.get(kind, "the email") if kind else "the email"


def finding_words(statement: str) -> str:
    """A rules-engine finding in plain words; any other finding as recorded."""
    if statement.startswith(B3_PROVED):
        error = statement.removeprefix(B3_PROVED).strip()
        return f"code proved one definite error, a {words(error)}."
    code = statement.partition(":")[0].strip()
    meaning = RULE.get(code)
    return f"{meaning[0].lower()}{meaning[1:]}" if meaning else statement


def recommends(rec: Recommendation | None, names: dict[str, str] | None = None) -> str:
    """The left half: what the agent recommends, who proposed it, and its reasoning.

    `names` (evidence id -> what it is) puts evidence in words; the exact text is kept below.
    """
    from dispute_agent.ui.trail import plain

    if rec is None:
        return '<h3>What the agent recommends</h3><p class="muted">No proposal is recorded for this item.</p>'
    names = names or {}
    found = "".join(
        f"<li>{escape(kind)}: {escape(plain(finding_words(text), names))}</li>"
        for kind, text, _ in rec.findings
    )
    reasoning = plain(rec.rationale, names)
    if reasoning == rec.rationale:
        said = recorded("Reasoning, exactly as recorded:", rec.rationale)
    else:
        said = recorded("Reasoning, with evidence named in words:", reasoning) + (
            '<details class="more"><summary>Exactly as recorded</summary>'
            f"{recorded('', rec.rationale)}</details>"
        )
    codes = "".join(
        f"<li>{escape(text.partition(':')[0])} · cites {escape(', '.join(refs))}</li>"
        for _kind, text, refs in rec.findings
        if refs
    )
    style = "ai" if rec.by.startswith("AI") else "rules"
    return (
        "<h3>What the agent recommends</h3>"
        f"<p><strong>{escape(rec.action)}</strong>, {escape(rec.confidence)} confidence</p>"
        f'<p class="muted by by--{style}">Proposed by the {escape(rec.by)}</p>'
        + said
        + (f'<p class="caption">Findings:</p><ul class="checks">{found}</ul>' if found else "")
        + (
            f'<details class="more"><summary>Codes behind the findings</summary><ul>{codes}</ul></details>'
            if codes
            else ""
        )
    )


def if_approve_credit(
    item: PendingItem,
    *,
    proposer: str,
    duplicate: str | None,
    already: str,
    remeasured: str | None = None,
) -> str:
    """The right half for a credit: the exact amount, how code set it, and its figures.

    `remeasured` is the charge on the hub's re-measurement, for a remeasurement credit. Its
    figures then name the first recalculation for what it is, and leave out its overcharge:
    "Overcharge ₹0.00" under "A credit of ₹76.00" read as a contradiction (live QA,
    2026-11-23).
    """
    reason_words = words(item.credit_reason or "")
    if duplicate is not None:
        how = "the second debit of the same weight charge, less anything already credited on this shipment"
        rows: list[tuple[str, str]] = [
            ("Duplicate debit", duplicate),
            ("Already credited on this shipment", already),
        ]
        tail = '<p class="caption">The weight charge itself was correct:</p>'
    else:
        how = "the charge, less the lowest proven correct charge, less anything already credited on this shipment"
        rows = [("Already credited on this shipment", already)]
        tail = ""
    figures = [(label.capitalize(), value) for label, value in item.evidence]
    if remeasured is not None and duplicate is None:
        rows.append(("Charge on the hub's re-measurement", remeasured))
        figures = [
            ("Charge on the hub's first measurement", value)
            if label.lower() == "recalculated charge"
            else (label, value)
            for label, value in figures
            if label.lower() != "overcharge"
        ]
    return (
        "<h3>If you approve</h3>"
        f'<p>A credit of</p><p class="figure-lg">{escape(item.credit_amount)}</p>'
        f"<p>is posted to the ledger for {escape(reason_words)}.</p>"
        f'<p class="muted">Code set the amount: {escape(how)}. The {escape(proposer)} chose to '
        "propose a credit; it did not choose the amount.</p>"
        "<h4>Code's figures behind it</h4>"
        + pairs(rows, numeric=True)
        + tail
        + pairs(figures, numeric=True)
    )


def marked_body(body: str, numbers: Sequence[tuple[str, str]]) -> str:
    """The stored body, escaped, with each cited number marked and indexed in order."""
    text = escape(body)
    marks: dict[str, str] = {}
    for i, (value, _source) in enumerate(numbers, 1):
        shown = escape(value)
        # No digits in the token: a later figure such as "6" matched inside "\x006\x00" and
        # broke an earlier mark. Private-use characters never appear in a body.
        token = f"\x00{chr(0xE000 + i)}\x00"
        if shown in text:
            text = text.replace(shown, token, 1)
            marks[token] = f'<span class="num-mark">{shown}</span><sup>{i}</sup>'
    for token, html in marks.items():
        text = text.replace(token, html)
    return text.replace("\r\n", "\n").replace("\n", "&#10;")


def if_approve_email(item: PendingItem) -> str:
    """The right half for an email: the exact recipient, subject and body, and each number's
    source. Nothing is reworded: this is the text the hash binds (I13)."""
    rows = "".join(
        f'<li><span class="idx">{i}</span><span class="mono">{escape(value)}</span>'
        f"<span>{escape(source)}</span></li>"
        for i, (value, source) in enumerate(item.numbers, 1)
    )
    # No figures: a plain sentence, not a one-item list (the list's first column is a narrow
    # number badge, and a sentence squeezed into it wraps a letter or two per line).
    sources = (
        f'<ol class="sources">{rows}</ol>'
        if rows
        else '<p class="muted">This email has no figures.</p>'
    )
    return (
        "<h3>If you approve, this exact email is sent</h3>"
        f'<dl class="pairs"><dt>To</dt><dd>{escape(item.to)} <span class="muted">{TEST_ADDRESS}</span></dd>'
        f"<dt>Subject</dt><dd>{escape(item.subject)}</dd></dl>"
        f'<div class="mail">{marked_body(item.body, item.numbers)}</div>'
        f"<h4>Every number in it, and where it came from</h4>{sources}"
    )


def person_reason(code: ReviewCode, detail: str) -> str:
    """Why a case is with a person: the reason in words, a plain sentence, the recorded detail.

    Folded: the At-a-glance strip already says why in one line, so this is the detail behind
    it, opened on demand rather than repeated at full height.
    """
    sentence, _rule = handed_over(code, detail)
    plain = sentence.partition(": ")[2]
    plain = f"{plain[0].upper()}{plain[1:]}" if plain else ""
    return (
        '<details class="more why-fold"><summary>Why it is with a person: '
        f"{escape(reason(code))}</summary>"
        + (f"<p>{escape(plain)}</p>" if plain else "")
        + (recorded("The detail, exactly as recorded:", detail) if detail else "")
        + "</details>"
    )


def at_a_glance(
    *,
    why: str,
    why_detail: str,
    money: Sequence[tuple[str, str]],
    latest: str,
    latest_detail: str,
    proposed: str,
    proposed_by: str,
) -> str:
    """The four things a reviewer needs before anything else, side by side.

    Why the case is here, what the money looks like in code's figures, the latest word from
    outside (the hub or the seller), and what the agent last proposed. Everything below it
    on the card is the detail behind one of these.
    """

    def tile(label: str, main: str, sub: str) -> str:
        return (
            f'<div class="glance-tile"><p class="glance-label">{escape(label)}</p>'
            f'<p class="glance-main">{escape(main)}</p>'
            + (f'<p class="glance-sub">{escape(sub)}</p>' if sub else "")
            + "</div>"
        )

    figures = "".join(
        f'<div class="glance-fig"><span>{escape(k)}</span><b>{escape(v)}</b></div>'
        for k, v in money
    )
    return (
        '<section class="glance" aria-label="At a glance">'
        + tile("Why it is here", why, why_detail)
        + '<div class="glance-tile"><p class="glance-label">The money, worked out by code</p>'
        + f'<div class="glance-figs">{figures}</div></div>'
        + tile("Latest from outside", latest, latest_detail)
        + tile("The agent proposed", proposed, proposed_by)
        + "</section>"
    )


def last_proposal(rec: Recommendation | None) -> str:
    """What the agent (or the rules engine) last proposed, as context for the person."""
    last = (
        f"<p><strong>{escape(rec.action)}</strong>, by {escape(rec.by)}</p>"
        if rec is not None
        else '<p class="muted">No proposal is recorded.</p>'
    )
    return f"<h4>The agent's last proposal</h4>{last}"


__all__ = [
    "TEST_ADDRESS",
    "approve_label",
    "at_a_glance",
    "finding_words",
    "if_approve_credit",
    "if_approve_email",
    "kind_title",
    "last_proposal",
    "marked_body",
    "person_reason",
    "recommends",
    "what",
]
