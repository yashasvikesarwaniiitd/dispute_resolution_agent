"""The one password in front of the hosted console (ADR 0045).

The hosted console sends real email and opens real Jira issues, so nobody reaches a screen
without the password the owner set as a secret. It fails closed: a hosted console with no
password configured does not open at all. On the owner's own machine (`make run-console`)
there is no gate; that console listens on loopback only.

The password is compared in constant time and never kept: the session remembers only that
the gate was passed. Approvals still need the review console token on the Review screen.
"""

from __future__ import annotations

import hmac


def opens(provided: str | None, expected: str | None) -> bool:
    """True only for the configured password. No password configured opens nothing."""
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


__all__ = ["opens"]
