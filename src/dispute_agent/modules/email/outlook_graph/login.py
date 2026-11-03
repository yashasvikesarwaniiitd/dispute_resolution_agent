"""`make email-login`: sign in once, interactively (ADR 0023).

Deliberately a separate command. The device code flow needs a person at a browser, and a
send that paused to wait for one would look to an operator exactly like a hang — so the
unattended path never initiates sign-in. It raises `reauth_required` and stops, and this is
what the owner runs in response.

Nothing here prints a token. The device-code message MSAL produces contains a URL and a
short user code, neither of which is a credential.
"""

from __future__ import annotations

import sys

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError
from dispute_agent.modules.email.outlook_graph.auth import SCOPES, GraphAuth


def main() -> int:
    settings = Settings()
    if not settings.graph_client_id:
        print(
            "GRAPH_CLIENT_ID is not set. See docs/SETUP_CHANNELS.md section 1b.",
            file=sys.stderr,
        )
        return 2

    auth = GraphAuth(
        client_id=settings.graph_client_id,
        authority=settings.graph_authority,
        cache_path=settings.graph_token_cache_path,
    )

    print(f"Signing in for {settings.graph_mailbox or 'the pilot mailbox'}.")
    print(f"Permissions requested: {', '.join(SCOPES)} (plus offline_access).\n")

    try:
        auth.device_login(prompt=print)
    except (AuthPortError, ConfigError) as failed:
        print(f"\nSign-in failed: {failed}", file=sys.stderr)
        return 1

    print(f"\nSigned in. The token cache is at {settings.graph_token_cache_path}.")
    print("That file is a secret: it is encrypted at rest and git-ignored. Do not copy it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
