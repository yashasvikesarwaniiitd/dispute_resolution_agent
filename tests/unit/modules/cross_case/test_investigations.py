"""P7-05: the parent investigation goes through policy and the ActionExecutor, once."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import pytest

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.executor.executor import ActionExecutor
from dispute_agent.kernel.policy.config import PolicyConfig
from dispute_agent.kernel.policy.engine import PolicyEngine
from dispute_agent.kernel.policy.rules import phase_5_rules
from dispute_agent.modules.cross_case.analyzer import PatternAnalyzer
from dispute_agent.modules.cross_case.analyzer.investigations import (
    LABEL,
    Investigations,
    PatternActionStore,
)
from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import cluster_members, fixture
from dispute_agent.modules.evidence.synthetic_shipments.library import SyntheticLibrarySource
from dispute_agent.modules.ticketing.fake.ticketing import FakeTicketingSystem
from dispute_agent.modules.ticketing.write_port import IssueWritePort
from tests.support.patterns import T0, add_case, new_engine

DAY = timedelta(days=1)
MODULES = {"ticketing": True, "evidence": True, "reasoning": True, "email": True, "ledger": True}


@dataclass
class Switch:
    on: bool = False

    def is_engaged(self) -> bool:
        return self.on


@dataclass
class Setup:
    investigations: Investigations
    tickets: FakeTicketingSystem
    pattern_id: str
    switch: Switch
    clock: FakeClock


def setup() -> Setup:
    engine = new_engine()
    for n, member in enumerate(cluster_members(fixture("hub_scanner_cluster"))[:5]):
        add_case(
            engine,
            f"case_{n:02d}",
            claim=member.claim_ref,
            shipment=member.ref,
            seller=member.seller_id,
            opened_at=T0 + n * 2 * DAY,
        )
    ids = FakeIdGenerator()
    clock = FakeClock(T0 + 11 * DAY)
    repo = PatternRepository(engine)
    analyzer = PatternAnalyzer(repo, SyntheticLibrarySource().keys_for, ids)
    [pattern_id] = analyzer.run(clock.now()).created
    tickets = FakeTicketingSystem(now=clock.now())
    switch = Switch()
    store = PatternActionStore(engine)
    investigations = Investigations(
        engine=engine,
        repository=repo,
        executor=ActionExecutor(PolicyEngine(phase_5_rules(), clock, ids), store, switch),
        actions=store,
        port=lambda subject: IssueWritePort(tickets, subject, "WD"),
        ids=ids,
        policy=PolicyConfig(),
        modules=MODULES,
    )
    return Setup(investigations, tickets, pattern_id, switch, clock)


def test_one_run_opens_one_issue_verified_by_read_back() -> None:
    s = setup()
    result = s.investigations.open(s.pattern_id, s.clock.now())
    assert (result.status, result.external_ref) == ("VERIFIED", "WD-1")
    assert s.tickets.issue_count() == 1
    [issue] = s.tickets._issues
    assert LABEL in issue.spec.labels
    assert "not a finding" in issue.spec.description


def test_a_second_run_updates_rather_than_creating_a_second_issue() -> None:
    s = setup()
    s.investigations.open(s.pattern_id, s.clock.now())
    again = s.investigations.open(s.pattern_id, s.clock.now() + DAY)
    assert (again.status, again.reason) == ("VERIFIED", "already_open")
    assert s.tickets.issue_count() == 1


def test_the_kill_switch_stops_it_inside_the_executor() -> None:
    s = setup()
    s.switch.on = True
    result = s.investigations.open(s.pattern_id, s.clock.now())
    assert result.status == "REFUSED"
    assert s.tickets.issue_count() == 0


def test_a_dismissed_pattern_opens_nothing() -> None:
    s = setup()
    s.investigations.repository.set_status(s.pattern_id, "DISMISSED", "ops", s.clock.now())
    assert s.investigations.open(s.pattern_id, s.clock.now()).status == "SKIPPED"
    assert s.tickets.issue_count() == 0


def test_a_failed_write_is_retried_on_the_next_run_and_still_makes_one_issue() -> None:
    s = setup()
    s.tickets.faults["upsert_issue"] = "permanent"
    first = s.investigations.open(s.pattern_id, s.clock.now())
    assert first.status == "FAILED"
    s.tickets.faults.clear()
    second = s.investigations.open(s.pattern_id, s.clock.now() + DAY)
    assert second.status == "VERIFIED"
    assert s.tickets.issue_count() == 1


def test_the_store_refuses_anything_but_an_issue() -> None:
    from dispute_agent.kernel.domain.errors import PolicyRefused

    s = setup()
    s.investigations.open(s.pattern_id, s.clock.now())
    [action] = s.investigations.actions.for_pattern(s.pattern_id)
    from dispute_agent.kernel.domain.enums import ActionType

    credit_like: Any = action.model_copy(update={"action_type": ActionType.SEND_MESSAGE})
    with pytest.raises(PolicyRefused):
        s.investigations.actions.insert(credit_like)
