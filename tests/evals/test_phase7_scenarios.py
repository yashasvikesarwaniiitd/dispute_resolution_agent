"""P7-10: scenarios S15 and S16 on the running system, with cross_case on.

The same assembly as `make run-api` (`build_adapters` and `_build_runtime` in app.py): the
whole line, the runner, and the cross-case job it runs - with a clock the test moves.

- **S15** (`hub_scanner_cluster`): 5 disputes over 10 days, the analyzer, a 6th dispute. One
  UNCONFIRMED pattern; one linked Jira investigation, through the executor and read back; the
  6th case is briefed with the pattern and its verdict is at least REQUIRE_APPROVAL; nothing
  is credited or upheld because of the pattern.
- **S16** (`seasonal_bulky_sku`): a pattern that looks suspicious and is legitimate. It is
  dismissed on the ops finding alone - re-running the analysis never confirms it - and nothing
  is credited or penalised because of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text

from dispute_agent.api.events import RaiseDispute
from dispute_agent.app import _build_runtime, build_adapters, load_modules_file
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.kernel.domain.enums import ActorKind, InboundEventType
from dispute_agent.kernel.domain.events import InboundEvent, InboundPayload
from dispute_agent.kernel.domain.external import OpsFinding
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.registry.validation import validate_modules
from dispute_agent.modules.cross_case.analyzer.investigations import LABEL
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import (
    SELLER_CONTACT,
    cluster_members,
    fixture,
)

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)

MODULES = """
modules:
  evidence:      {enabled: true,  adapter: synthetic_shipments}
  model_gateway: {enabled: false, adapter: fake}
  reasoning:     {enabled: true,  adapter: rules_only}
  input_screen:  {enabled: true,  adapter: rules_based}
  tracing:       {enabled: true,  adapter: noop}
  image_check:   {enabled: false, adapter: recorded}
  email:         {enabled: true,  adapter: fake}
  ticketing:     {enabled: true,  adapter: fake}
  ledger:        {enabled: true,  adapter: simulated}
  approvals:     {enabled: true,  adapter: store_backed}
  cross_case:    {enabled: true,  adapter: sql_context}
  evals:         {enabled: true,  adapter: default}
"""


@dataclass
class World:
    runtime: Any
    adapters: Any
    clock: FakeClock
    engine: Any
    briefings: list[Any]

    def dispute(self, shipment: Any, n: int) -> str:
        body = RaiseDispute(
            source_event_id=f"form-{shipment.ref}-{n}",
            shipment_ref=shipment.ref,
            claim_ref=shipment.claim_ref,
            seller_ref=shipment.seller_id,
            seller_contact=SELLER_CONTACT,
            raised_at=self.clock.now(),
        )
        event = InboundEvent(
            type=InboundEventType.DISPUTE_RAISED,
            source=body.source,
            source_event_id=body.source_event_id,
            occurred_at=body.raised_at,
            actor=Actor(kind=ActorKind.EXTERNAL, id=body.source),
            payload=InboundPayload(),
        )
        result = self.runtime.submit_dispute(event, body)
        assert result.case_id is not None
        return str(result.case_id)

    def rows(self, sql: str, **params: Any) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(text(sql), params))

    def issues(self) -> list[Any]:
        return [i for i in self.adapters.ticketing._issues if LABEL in i.spec.labels]


def world(tmp_path: Path) -> World:
    (tmp_path / "modules.yaml").write_text(MODULES, encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    engine = create_sqlite_engine(database)
    apply_schema(engine, schema_sql_path().read_text(encoding="utf-8"))
    registry = validate_modules(load_modules_file(tmp_path), app_env="test")
    clock = FakeClock(T0)
    ids = UlidIdGenerator()
    adapters = build_adapters(registry, clock=clock, ids=ids, data_dir=tmp_path, database=database)
    settings = Settings(app_env="test", test_seller_email=SELLER_CONTACT)
    runtime = _build_runtime(tmp_path, database, settings, clock, ids, adapters, registry)

    briefings: list[Any] = []
    from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
    from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

    baseline = RulesOnlyReasoner(pack=WeightDisputePack(), ids=ids, clock=clock)

    class Recording:
        def propose(self, briefing: Any) -> Any:
            briefings.append(briefing)
            return baseline.propose(briefing)

    runtime.runner.line.reasoner = Recording()
    return World(runtime, adapters, clock, engine, briefings)


def no_pattern_credit_or_uphold(w: World) -> None:
    assert w.adapters.ledger.credit_count() == 0
    upheld = w.rows("SELECT id FROM cases WHERE state = 'RESOLVED_UPHELD'")
    assert upheld == []


def test_s15_a_recurring_anomaly_opens_one_linked_investigation(tmp_path: Path) -> None:
    w = world(tmp_path)
    members = cluster_members(fixture("hub_scanner_cluster"))
    for n, shipment in enumerate(members[:5]):
        w.dispute(shipment, n)
        w.clock.advance(2 * DAY)  # five disputes over 10 days
    assert all(not b.pattern_context for b in w.briefings)  # no pattern existed yet

    w.clock.advance(DAY)  # day 11: the analyzer runs on the runner's tick
    report = w.runtime.runner.tick()
    assert report.jobs_run == ["cross_case"] and report.failures == []

    patterns = w.rows("SELECT id, dimension, match_value, status FROM pattern_records")
    assert [(p[1], p[2], p[3]) for p in patterns] == [("scanner", "SCN-03-2", "UNCONFIRMED")]
    assert len(w.issues()) == 1  # one parent investigation
    [action] = w.rows("SELECT status FROM pattern_actions")
    assert action[0] == "VERIFIED"  # through the executor, read back
    [investigation] = w.rows("SELECT status, external_ref FROM pattern_investigations")
    assert investigation[0] == "VERIFIED"

    w.clock.advance(DAY)  # day 12: the 6th dispute
    sixth = w.dispute(members[5], 5)
    briefing = w.briefings[-1]
    assert [str(p.id) for p in briefing.pattern_context] == [patterns[0][0]]
    # At least REQUIRE_APPROVAL: the case waits for a person and nothing went out on its own.
    # (That a pattern lifts an ALLOWed action to a person is proven in drills/test_hook_a.py.)
    [(state,)] = w.rows("SELECT state FROM cases WHERE id = :c", c=sixth)
    assert state in ("AWAITING_APPROVAL", "NEEDS_HUMAN_REVIEW")
    pending = w.rows(
        "SELECT id FROM approvals WHERE case_id = :c AND decision = 'PENDING'", c=sixth
    )
    assert state == "NEEDS_HUMAN_REVIEW" or len(pending) == 1
    assert w.adapters.email.sent_count() == 0

    # A second analysis run updates; it never opens a second issue.
    w.clock.advance(2 * DAY)
    w.runtime.runner.tick()
    assert len(w.issues()) == 1
    no_pattern_credit_or_uphold(w)


def test_s16_a_legitimate_pattern_is_dismissed_on_ops_evidence_only(tmp_path: Path) -> None:
    w = world(tmp_path)
    for n, shipment in enumerate(cluster_members(fixture("seasonal_bulky_sku"))):
        w.dispute(shipment, n)
        w.clock.advance(timedelta(hours=12))

    w.runtime.runner.tick()
    [(_pattern_id, dimension, status)] = w.rows("SELECT id, dimension, status FROM pattern_records")
    assert (dimension, status) == ("sku", "UNCONFIRMED")
    [issue] = w.issues()

    # More analysis runs, with no finding: nothing about the cases can confirm it.
    for _ in range(3):
        w.clock.advance(2 * DAY)
        w.runtime.runner.tick()
    assert w.rows("SELECT status FROM pattern_records")[0][0] == "UNCONFIRMED"

    # Ops inspects and finds the measurements right: the pattern is legitimate.
    w.adapters.ticketing.post_finding(
        OpsFinding(
            issue_key=issue.ref.key,
            finding_id="F-1",
            kind="no_fault_found",
            at=w.clock.now(),
        )
    )
    w.clock.advance(2 * DAY)
    w.runtime.runner.tick()
    [(status, reason, refs)] = w.rows(
        "SELECT status, status_reason, evidence_refs_json FROM pattern_records"
    )
    assert (status, reason, json.loads(refs)) == ("DISMISSED", "ops:no_fault_found", ["F-1"])

    # A dismissed pattern briefs no one: the next dispute is decided on its own evidence.
    w.dispute(cluster_members(fixture("seasonal_bulky_sku"))[0], 99)
    assert not w.briefings[-1].pattern_context
    no_pattern_credit_or_uphold(w)
