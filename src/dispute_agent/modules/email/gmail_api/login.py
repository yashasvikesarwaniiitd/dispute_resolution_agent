"""`make gmail-login`: sign in once, in the browser (ADR 0038).

A separate command, like `make email-login` for Outlook: the sign-in needs a person at a
browser, so the unattended path never starts one. It raises `reauth_required` and stops, and
this is what the owner runs in response. While the Google app is in "Testing" status the
sign-in lasts 7 days.

Nothing here prints a token. The consent URL contains the client id, a PKCE challenge and a
random state - none of them a credential.
"""

from __future__ import annotations

import sys
import webbrowser

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError, TransientPortError
from dispute_agent.modules.email.gmail_api.auth import SCOPES, GmailAuth


def main() -> int:
    settings = Settings()
    secret = settings.gmail_client_secret
    if not settings.gmail_client_id or secret is None:
        print(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are not set. See docs/SETUP_CHANNELS.md.",
            file=sys.stderr,
        )
        return 2
    auth = GmailAuth(
        client_id=settings.gmail_client_id,
        client_secret=secret.get_secret_value(),
        cache_path=settings.gmail_token_cache_path,
        timeout_s=settings.http_timeout_s,
    )

    print(f"Signing in for {settings.gmail_mailbox or 'the pilot Gmail'}.")
    print(f"Permissions requested: {', '.join(s.rsplit('/', 1)[-1] for s in SCOPES)}.")
    print("A browser tab will open. Sign in with the pilot Gmail and allow access.\n")

    def open_browser(url: str) -> None:
        if not webbrowser.open(url):
            print(f"Open this address in your browser:\n{url}\n")

    try:
        auth.browser_login(open_browser=open_browser, login_hint=settings.gmail_mailbox)
    except (AuthPortError, ConfigError, TransientPortError) as failed:
        print(f"\nSign-in failed: {failed}", file=sys.stderr)
        return 1

    print(f"\nSigned in. The token file is at {settings.gmail_token_cache_path}.")
    print("That file is a secret: it is encrypted at rest and git-ignored. Do not copy it.")
    print("While your Google app is in Testing status, sign in again every 7 days.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
