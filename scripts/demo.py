"""`make demo`: one synthetic dispute, end to end, against the real test accounts.

What this is for: showing that the pieces actually connect to something outside the process.
Everything it does is already covered by tests against fakes; what it adds is a real email
in a real mailbox and a real issue in a real project, both verified by reading them back.

It refuses to run without credentials rather than quietly demonstrating the fakes. A demo
that silently proves nothing is worse than one that does not start, because somebody will
show it to someone.
"""

from __future__ import annotations

from dispute_agent.infra.settings import Settings

REQUIRED = {
    "GRAPH_CLIENT_ID": "the Entra app registration (docs/SETUP_CHANNELS.md section 1)",
    "GRAPH_MAILBOX": "the pilot mailbox",
    "TEST_SELLER_EMAIL": "the address that stands in for the seller",
    "JIRA_BASE_URL": "the Jira site",
    "JIRA_EMAIL": "the Atlassian account the API token belongs to",
    "JIRA_API_TOKEN": "the Jira API token",
    "JIRA_PROJECT_KEY": "the test project",
    "REVIEW_CONSOLE_TOKEN": "the token that protects the review queue",
}


def missing_settings(settings: Settings) -> list[str]:
    """Which prerequisites are absent, named as the owner would recognise them."""
    absent = []
    for name, what in REQUIRED.items():
        value = getattr(settings, name.lower(), None)
        if value is None or (isinstance(value, str) and not value.strip()):
            absent.append(f"  {name} - {what}")
    return absent


def main() -> int:
    settings = Settings()
    absent = missing_settings(settings)
    if absent:
        print("make demo sends real email and creates a real Jira issue.")
        print("It needs accounts that only you can create. Missing:\n")
        print("\n".join(absent))
        print("\nSee docs/SETUP_CHANNELS.md, then run `make email-login` and try again.")
        return 2

    print("Credentials are present.")
    print(
        "The end-to-end run is wired in card P3-10's remaining work: it needs the "
        "orchestrator steps for investigate and execute, which arrive in Phase 4."
    )
    print("Until then, `make test-integration` exercises both real adapters directly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
