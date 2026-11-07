"""The live demo's wiring, proven without the network.

`make demo` without `--offline` runs a whole case on the owner's real accounts (`Live`). This
runs the same thing with the real Gmail adapter talking to an in-memory Gmail over a mock
HTTP transport: the actual adapter code end to end through the case, nothing sent anywhere.
It shows the case completes through a real adapter, that the only address ever written to
is the owner's test seller (I11), and that the report counts what this run verified rather
than whatever else a real mailbox already holds.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dispute_agent.api.full_run import Live, run_full
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.policy.config import PolicyConfig
from tests.unit.modules.email.test_gmail_api import Gmail

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
OWNER_SELLER = "owner+seller@example.test"


def _recipients(gmail: Gmail) -> set[str]:
    return {
        h["value"]
        for m in gmail.messages.values()
        for h in m["payload"]["headers"]
        if h["name"] == "To"
    }


def test_a_whole_case_runs_through_the_real_gmail_adapter() -> None:
    gmail = Gmail()
    report = run_full(
        "calc_error_wrong_slab",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        live=Live(seller_contact=OWNER_SELLER, channel=gmail.channel(), wait_s=0),
    )
    assert report.case.state is CaseState.RESOLVED_CREDITED
    assert report.ledger_credits == 1
    # Sent once, verified once, and counted from the case's own verified actions.
    assert gmail.sends == report.emails_sent == 1
    assert report.subjects and all(s for s in report.subjects)


def test_the_only_address_written_to_is_the_owner_s_test_seller() -> None:
    gmail = Gmail()
    run_full(
        "calc_error_wrong_slab",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        live=Live(seller_contact=OWNER_SELLER, channel=gmail.channel(), wait_s=0),
    )
    assert _recipients(gmail) == {OWNER_SELLER}


def test_what_the_mailbox_already_held_is_not_counted_as_this_run_s() -> None:
    gmail = Gmail()
    gmail.deliver("someone@example.test", "an old email", "hello", NOW, "<old@example.test>")
    report = run_full(
        "calc_error_wrong_slab",
        policy=PolicyConfig(),
        clock=FakeClock(NOW),
        ids=FakeIdGenerator(),
        live=Live(seller_contact=OWNER_SELLER, channel=gmail.channel(), wait_s=0),
    )
    assert report.emails_sent == 1
