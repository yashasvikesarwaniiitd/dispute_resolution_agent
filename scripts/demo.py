"""`make demo`: one synthetic dispute, end to end.

`make demo ARGS="--offline [case] [--agent]"` runs the whole single-case sequence (P5-09) -
dispute, evidence, decision, your approval, the action, its read-back, the follow-up and its
approval, the outcome - against the email and Jira fakes and the real simulated ledger, with
a scripted "yes" at each approval, and prints the timeline. `--agent` lets your Foundry agent
make the decision (about half a cent). Nothing is sent anywhere.

Without `--offline`, the demo is for the real test accounts:

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


def offline(case: str, *, agent: bool) -> int:
    """The full sequence against fakes and the simulated ledger; prints a readable timeline."""
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
    report = run_full(case, policy=policy, clock=clock, ids=ids, reasoner=reasoner)

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
    print("\nWhat went out (to the fakes, never the internet):")
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
    args = parser.parse_args()
    if args.offline:
        return offline(args.case, agent=args.agent)

    settings = Settings()
    absent = missing_settings(settings)
    if absent:
        print("make demo sends real email and creates a real Jira issue.")
        print("It needs accounts that only you can create. Missing:\n")
        print("\n".join(absent))
        print("\nSee docs/SETUP_CHANNELS.md, then run `make email-login` and try again.")
        print('Meanwhile: make demo ARGS="--offline" runs the whole sequence against fakes.')
        return 2

    print("Credentials are present.")
    print(
        "The live run against Outlook and Jira needs the real adapters wired into the full "
        "run (BACKLOG: Phase 5 live demo). Until then, `make test-integration` exercises "
        'both real adapters directly, and make demo ARGS="--offline" the whole sequence.'
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
