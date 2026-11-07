"""`make gmail-export`: put the Gmail refresh token on the clipboard, for the hosted console.

ADR 0045. The hosted console cannot read the encrypted token file (DPAPI on Windows does not
open on Linux), so the owner pastes the token into the host's secret store as
GMAIL_REFRESH_TOKEN. This copies it to the clipboard and prints nothing of it: not to the
screen, not to a log, not to a file. Run `make gmail-login` first; repeat both every 7 days
while the Google app is in Testing status.
"""

from __future__ import annotations

import subprocess
import sys

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError
from dispute_agent.modules.email.gmail_api.auth import GmailAuth


def main() -> int:
    settings = Settings()
    secret = settings.gmail_client_secret
    if not (settings.gmail_client_id and secret):
        print("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET are not set; see docs/SETUP_CHANNELS.md")
        return 2
    auth = GmailAuth(
        client_id=settings.gmail_client_id,
        client_secret=secret.get_secret_value(),
        cache_path=settings.gmail_token_cache_path,
    )
    try:
        refresh = auth._load_refresh()  # the one place a raw token leaves the encrypted file
    except (AuthPortError, ConfigError) as failed:
        print(f"Cannot read the Gmail sign-in: {failed}")
        return 2
    if not refresh:
        print("Not signed in to Gmail. Run `make gmail-login` first.")
        return 2
    if sys.platform != "win32":
        print("This copies with Windows' `clip`; on another system, extend this script.")
        return 2
    subprocess.run(["clip"], input=refresh.encode("utf-8"), check=True)
    print(
        "Copied the Gmail refresh token to the clipboard (not shown). Paste it into the "
        "hosted app's Secrets as GMAIL_REFRESH_TOKEN, then clear the clipboard by copying "
        "something else. It lasts until the Gmail sign-in expires (7 days in Testing)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
