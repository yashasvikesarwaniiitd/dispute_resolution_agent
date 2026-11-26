"""Whether two email addresses name the same mailbox (ADR 0053).

Gmail delivers `name+anything@gmail.com` and `n.a.m.e@gmail.com` to `name@gmail.com`: the
plus part and the dots in the local part are not part of the mailbox (Google's "use an alias
in Gmail" help page). A seller whose contact is `name+seller@gmail.com` replies from
`name@gmail.com`, so an exact comparison refused every real reply to the test seller.

Only Gmail's own domains are folded, because only Google documents this rule for them. Every
other address is compared exactly, as before: for any other provider a plus part or a dot
may name a different person, and a reply from a different person must never be routed to a
seller's case (orchestrator.md section 2).
"""

from __future__ import annotations

import re

from dispute_agent.kernel.domain.values import UntrustedText

GMAIL_DOMAINS = frozenset({"gmail.com", "googlemail.com"})


def mailbox(address: str) -> str:
    """The address as its mailbox: lower case, and for Gmail without dots or a plus part."""
    cleaned = address.strip().lower()
    local, at, domain = cleaned.rpartition("@")
    if not at or not local or domain not in GMAIL_DOMAINS:
        return cleaned
    local = local.split("+", 1)[0].replace(".", "")
    return f"{local}@gmail.com"


def same_mailbox(a: str, b: str) -> bool:
    """True when both addresses deliver to one mailbox. An empty address matches nothing."""
    if not a.strip() or not b.strip():
        return False
    return mailbox(a) == mailbox(b)


#: Where a mail client starts quoting the message being replied to (ADR 0055): Outlook's
#: "Original Message" rule or its "From: name <address>" block, and quoted ">" lines. A bare
#: "From:" without an address is a seller's own words ("From: our Pune warehouse").
_QUOTE_LINE = re.compile(
    r"^\s*(?:-{2,}\s*Original Message\s*-{2,}|From:\s.*@.*|>.*)\s*$", re.IGNORECASE
)
#: Gmail's "On <date>, <name> <address> wrote:", which it wraps over two or three lines when
#: the name and address are long.
_ON = re.compile(r"^\s*On\s", re.IGNORECASE)
_WROTE = re.compile(r"\bwrote:\s*$", re.IGNORECASE)
_WRAPPED = 3


def _quote_start(lines: list[str]) -> int | None:
    for i, line in enumerate(lines):
        if _QUOTE_LINE.match(line):
            return i
        if _ON.match(line) and any(_WROTE.search(x) for x in lines[i : i + _WRAPPED]):
            return i
    return None


def new_text(body: str) -> str:
    """The seller's own words in a reply: the whole body when nothing is quoted, else what
    they wrote above the quoted email - or, when the reply starts with the quote, the lines
    they wrote between its quoted lines (an inline answer).

    "" when they wrote nothing of their own. A reply that only attaches a photo is all quote,
    and that quote is our own email: shown as the seller's words, it read as their message
    and reached the reply reader as if they had written it (live, SHP-102026, 2026-11-27).
    """
    lines = body.replace("\r\n", "\n").split("\n")
    start = _quote_start(lines)
    if start is None:
        return body.strip()
    kept = "\n".join(lines[:start]).strip()
    return kept or _inline(lines[start:])


def _inline(quoted: list[str]) -> str:
    """A seller's words written between the quoted lines of a reply that starts with the quote.

    Only for ">" quoting (Gmail and most clients). Outlook's header block quotes the original
    message without marking its lines, so everything below it is ours and nothing is kept.
    """
    first = quoted[0]
    if _ON.match(first):  # "On <date>, <name> wrote:", over up to three lines
        end = next(i for i, line in enumerate(quoted[:_WRAPPED]) if _WROTE.search(line))
        quoted = quoted[end + 1 :]
    elif not first.lstrip().startswith(">"):
        return ""
    return "\n".join(line for line in quoted if not line.lstrip().startswith(">")).strip()


def reply_only(body: UntrustedText) -> UntrustedText:
    """The seller's own words, for the screen and the reader (ADR 0055).

    A mail client quotes the email being answered, and that email is ours. Screened with
    the reply, its "Name <address> wrote:" line read as markup and flagged every real Gmail
    reply as an instruction attempt. The quoted part is never the seller's evidence, so it
    is left out here; the full email stays in the inbox record.
    """
    kept = new_text(body.text)
    if kept == body.text.strip():
        return body
    # Everything before the quote arrived whole, so `create` re-derives `truncated` for it.
    return UntrustedText.create(kept, body.origin, body.source_ref)


__all__ = ["GMAIL_DOMAINS", "mailbox", "new_text", "reply_only", "same_mailbox"]
