"""`make hosted-secrets`: the hosted console's Secrets block, on the clipboard (ADR 0045).

Builds the TOML that Streamlit Community Cloud's "Secrets" box takes, from the owner's own
`.env` and Gmail sign-in, and copies it to the clipboard. No secret is printed. The one value
shown is the console password, because the owner has to know it to share it; it is shown in
the owner's terminal once and kept nowhere else (re-running makes a new one, unless
CONSOLE_PASSWORD is set in `.env`).

Run it yourself, in your own terminal: the password it prints is not for a chat window.
"""

from __future__ import annotations

import json
import secrets
import subprocess
import sys

from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import AuthPortError, ConfigError

#: Every setting the hosted console needs, in the order the block lists them.
NEEDED = (
    "MODULES_FILE",
    "CONSOLE_PASSWORD",
    "REVIEW_CONSOLE_TOKEN",
    "TEST_SELLER_EMAIL",
    "GMAIL_CLIENT_ID",
    "GMAIL_CLIENT_SECRET",
    "GMAIL_MAILBOX",
    "GMAIL_REFRESH_TOKEN",
    "JIRA_BASE_URL",
    "JIRA_EMAIL",
    "JIRA_API_TOKEN",
    "JIRA_PROJECT_KEY",
)


def toml_block(values: dict[str, str]) -> str:
    """`NAME = "value"` lines. JSON string escaping is valid TOML basic-string escaping."""
    return "\n".join(f"{name} = {json.dumps(values[name])}" for name in NEEDED) + "\n"


def collect(
    settings: Settings, refresh: str | None, password: str
) -> tuple[dict[str, str], list[str]]:
    """The values, and the names of any that are missing."""

    def plain(value: object) -> str:
        if value is None:
            return ""
        getter = getattr(value, "get_secret_value", None)
        return str(getter() if getter else value)

    values = {
        "MODULES_FILE": "modules.hosted.yaml",
        "CONSOLE_PASSWORD": password,
        "REVIEW_CONSOLE_TOKEN": plain(settings.review_console_token),
        "TEST_SELLER_EMAIL": plain(settings.test_seller_email),
        "GMAIL_CLIENT_ID": plain(settings.gmail_client_id),
        "GMAIL_CLIENT_SECRET": plain(settings.gmail_client_secret),
        "GMAIL_MAILBOX": plain(settings.gmail_mailbox),
        "GMAIL_REFRESH_TOKEN": refresh or "",
        "JIRA_BASE_URL": plain(settings.jira_base_url),
        "JIRA_EMAIL": plain(settings.jira_email),
        "JIRA_API_TOKEN": plain(settings.jira_api_token),
        "JIRA_PROJECT_KEY": plain(settings.jira_project_key),
    }
    return values, [name for name in NEEDED if not values[name].strip()]


def main() -> int:
    settings = Settings()
    refresh = None
    if settings.gmail_client_id and settings.gmail_client_secret:
        from dispute_agent.modules.email.gmail_api.auth import GmailAuth

        auth = GmailAuth(
            client_id=settings.gmail_client_id,
            client_secret=settings.gmail_client_secret.get_secret_value(),
            cache_path=settings.gmail_token_cache_path,
        )
        try:
            refresh = auth._load_refresh()  # leaves the encrypted file only for the clipboard
        except (AuthPortError, ConfigError):
            refresh = None
    given = settings.console_password
    password = given.get_secret_value() if given else secrets.token_urlsafe(12)
    values, missing = collect(settings, refresh, password)
    if missing:
        print("Not copied. These are missing from .env (or the Gmail sign-in):")
        print("\n".join(f"  {name}" for name in missing))
        print("GMAIL_REFRESH_TOKEN missing means: run `make gmail-login` first.")
        return 2
    if sys.platform != "win32":
        print("This copies with Windows' `clip`; on another system, extend this script.")
        return 2
    subprocess.run(["clip"], input=toml_block(values).encode("utf-8"), check=True)
    print(f"Copied the Secrets block ({len(NEEDED)} settings) to the clipboard. Not shown.")
    print("Paste it into Streamlit: your app > Settings > Secrets, then Save.")
    print(f"\nThe console password (give it to whoever should use the app): {password}")
    print("\nClear the clipboard afterwards by copying something else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
