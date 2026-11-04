"""`make foundry-login [--device-code]`: sign in to Azure once, without the Azure CLI.

Opens the browser at Microsoft's sign-in page for the directory in AZURE_TENANT_ID. You type
your password into Microsoft's page, never into this program. The token is kept in an
encrypted cache (DPAPI on Windows); later runs sign in silently until it expires.
"""

from __future__ import annotations

import argparse
import sys

from dispute_agent.app import FOUNDRY_AUTH_RECORD
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.modules.model_gateway.foundry_agent_service.signin import sign_in


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="make foundry-login")
    parser.add_argument(
        "--device-code",
        action="store_true",
        help="print a code to enter at microsoft.com/devicelogin instead of opening a browser",
    )
    args = parser.parse_args()
    settings = Settings()

    if args.device_code:
        print("A code will appear below. Open https://microsoft.com/devicelogin, enter it,")
        print("and sign in with the account that owns your Azure subscription.\n")
    else:
        print("A browser window will open. Sign in with the account that owns your Azure")
        print("subscription - if the browser offers your work account, choose another account.\n")
    try:
        account = sign_in(
            settings.azure_tenant_id, FOUNDRY_AUTH_RECORD, device_code=args.device_code
        )
    except KernelError as failed:
        print(f"foundry-login stopped: {failed}", file=sys.stderr)
        return 2
    except Exception as failed:
        print(f"Sign-in did not complete ({type(failed).__name__}).", file=sys.stderr)
        if not args.device_code:
            print(
                'If no browser opened, try: make foundry-login ARGS="--device-code"',
                file=sys.stderr,
            )
        return 1

    print(f"Signed in as {account}. The sign-in is saved, encrypted, for later runs.")
    print("Next: make foundry-check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
