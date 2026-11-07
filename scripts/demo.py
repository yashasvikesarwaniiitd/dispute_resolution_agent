"""`make demo`: one synthetic dispute, end to end.

`make demo ARGS="--offline [case] [--agent]"` runs the whole single-case sequence (P5-09) -
dispute, evidence, decision, your approval, the action, its read-back, the follow-up and its
approval, the outcome - against the email and Jira fakes and the real simulated ledger, with
a scripted "yes" at each approval, and prints the timeline. `--agent` lets your Foundry agent
make the decision (about half a cent). Nothing is sent anywhere.

Without `--offline` it is the same sequence on the owner's real accounts:

    make demo ARGS="calc_error_wrong_slab [--agent] [--jira]"

Email goes through the owner's Gmail (ADR 0038) to TEST_SELLER_EMAIL - the only address
policy allows - and every send is verified by reading it back from Sent. `--jira` uses the
real Jira test project too; without it Jira stays the stand-in. The ledger is always the
simulated one. A scripted reviewer still says yes to each approval.

It refuses to run without the accounts rather than quietly demonstrating the fakes. A demo
that silently proves nothing is worse than one that does not start, because somebody will
show it to someone.
"""

from __future__ import annotations

from dispute_agent.infra.settings import Settings

REQUIRED = {
    "GMAIL_CLIENT_ID": "the Google OAuth client (docs/SETUP_CHANNELS.md section 1-alt)",
    "GMAIL_CLIENT_SECRET": "the Google OAuth client secret",
    "GMAIL_MAILBOX": "the pilot Gmail",
    "TEST_SELLER_EMAIL": "the address that stands in for the seller",
}
REQUIRED_JIRA = {
    "JIRA_BASE_URL": "the Jira site",
    "JIRA_EMAIL": "the Atlassian account the API token belongs to",
    "JIRA_API_TOKEN": "the Jira API token",
    "JIRA_PROJECT_KEY": "the test project",
}


def missing_settings(settings: Settings, *, jira: bool = False) -> list[str]:
    """Which prerequisites are absent, named as the owner would recognise them."""
    absent = []
    wanted = {**REQUIRED, **(REQUIRED_JIRA if jira else {})}
    for name, what in wanted.items():
        value = getattr(settings, name.lower(), None)
        if value is None or (isinstance(value, str) and not value.strip()):
            absent.append(f"  {name} - {what}")
    return absent


def offline(case: str, *, agent: bool) -> int:
    """The full sequence against fakes and the simulated ledger; prints a readable timeline."""
    return run(case, agent=agent, live=None)


def live(case: str, *, agent: bool, jira: bool) -> int:
    """The same sequence with real email (Gmail) and, if asked, real Jira."""
    from dispute_agent.api.full_run import Live
    from dispute_agent.app import build_live_channels

    settings = Settings()
    absent = missing_settings(settings, jira=jira)
    if absent:
        print("The live demo sends a real email (and, with --jira, opens a real Jira issue).")
        print("It needs accounts that only you can create. Missing:\n")
        print("\n".join(absent))
        print("\nSee docs/SETUP_CHANNELS.md, then run `make gmail-login` and try again.")
        print('Meanwhile: make demo ARGS="--offline" runs the whole sequence against fakes.')
        return 2
    channel, tickets = build_live_channels(settings, jira=jira)
    if not channel.auth.signed_in():  # type: ignore[attr-defined]
        print("Gmail is not signed in (or the 7-day sign-in expired). Run `make gmail-login`.")
        return 2
    seller = str(settings.test_seller_email)
    print(f"Live: email through Gmail to {seller}; Jira {'real' if jira else 'stand-in'}.")
    return run(
        case, agent=agent, live=Live(seller_contact=seller, channel=channel, tickets=tickets)
    )


def run(case: str, *, agent: bool, live: object) -> int:
    from datetime import UTC, datetime

    from dispute_agent.api.full_run import run_full
    from dispute_agent.app import (
        DEFAULT_CONFIG_DIR,
        build_llm_reasoner,
        build_model_gateway,
        build_model_policy,
    )
    from dispute_agent.infra.clock import FakeClock
    from dispute_agent.infra.ids import UlidIdGenerator
    from dispute_agent.kernel.domain.errors import KernelError

    settings = Settings()
    clock, ids = FakeClock(datetime.now(UTC)), UlidIdGenerator()
    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    reasoner = None
    if agent:
        try:
            gateway = build_model_gateway(
                "foundry_agent_service", settings=settings, policy=policy, clock=clock, ids=ids
            )
            reasoner = build_llm_reasoner(
                gateway, settings=settings, policy=policy, clock=clock, ids=ids
            )
        except KernelError as failed:
            print(f"demo stopped: {failed}")
            return 2
    report = run_full(
        case,
        policy=policy,
        clock=clock,
        ids=ids,
        reasoner=reasoner,
        live=live,  # type: ignore[arg-type]
        max_rounds=30 if live else 12,
    )

    print(f"\nDemo: {case}   (decided by {'your Foundry agent' if agent else 'the rules engine'})")
    print("=" * 72)
    for proposal in report.proposals:
        print(
            f"Proposed: {proposal.action.value} ({proposal.confidence.value}, {proposal.reasoner})"
        )
    print("\nThe case's journey:")
    for n, state in enumerate(report.path, 1):
        print(f"  {n:2}. {state.value}")
    print("\nApprovals (a scripted reviewer said yes to each):")
    for approval in report.approvals:
        what = approval.request.subject.value.lower()
        amount = f" of {approval.request.amount}" if approval.request.amount else ""
        print(f"  - {what}{amount}: {approval.decision.value}")
    where = "real email through Gmail" if live else "to the fakes, never the internet"
    print(f"\nWhat went out ({where}):")
    for subject in report.subjects:
        print(f"  - email: {subject}")
    print(f"  - Jira issues: {report.jira_issues}")
    print(f"  - ledger credits: {report.ledger_credits}")
    print(f"\nFinal state: {report.case.state.value}")
    if report.case.review_reason is not None:
        print(f"  with a person because: {report.case.review_reason.code.value}")
    return 0


def main() -> int:
    import argparse
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    parser = argparse.ArgumentParser(prog="make demo")
    parser.add_argument("case", nargs="?", default="calc_error_wrong_slab")
    parser.add_argument("--offline", action="store_true", help="fakes and the simulated ledger")
    parser.add_argument("--agent", action="store_true", help="your Foundry agent decides")
    parser.add_argument("--jira", action="store_true", help="live run: real Jira too")
    args = parser.parse_args()
    if args.offline:
        return offline(args.case, agent=args.agent)

    return live(args.case, agent=args.agent, jira=args.jira)


if __name__ == "__main__":
    raise SystemExit(main())
