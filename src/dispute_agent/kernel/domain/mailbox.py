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


__all__ = ["GMAIL_DOMAINS", "mailbox", "same_mailbox"]
