"""P7-11 proof 2: cross_case is genuinely optional.

The same running system (`build_adapters` and `_build_runtime`), built with the module on and
off. With no pattern in play, every single-case dispute ends exactly the same way both ways,
and the runner's tick is clean. With it off there is no provider, no job and no pattern table
touched: the system is the Phase 6 system.

The release gates and the 57 recorded scenarios run on the scenario line, which takes no
patterns at all, so `make eval` is the module-off measurement by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from dispute_agent.api.events import RaiseDispute
from dispute_agent.app import _build_runtime, build_adapters, load_modules_file
from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import UlidIdGenerator
from dispute_agent.infra.settings import Settings
from dispute_agent.infra.sqlite.engine import apply_schema, create_sqlite_engine, schema_sql_path
from dispute_agent.kernel.domain.enums import ActorKind, InboundEventType
from dispute_agent.kernel.domain.events import InboundEvent, InboundPayload
from dispute_agent.kernel.domain.models import Actor
from dispute_agent.kernel.registry.validation import validate_modules
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import SELLER_CONTACT, fixture

ROOT = Path(__file__).resolve().parents[2]
T0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
FIXTURES = ["kurta_box_larger", "calc_error_high_value", "not_delivered", "ambiguous_image"]


def runtime(tmp_path: Path, on: bool) -> Any:
    shipped = (ROOT / "config" / "modules.yaml").read_text(encoding="utf-8")
    if not on:
        shipped = shipped.replace(
            "    enabled: true                                 # Phase 7 (P7-12)",
            "    enabled: false                                # Phase 7 (P7-12)",
        ).replace("    adapter: sql_context ", '    adapter: "null"    ')
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "modules.yaml").write_text(shipped, encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        (ROOT / "config" / "policy.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    database = tmp_path / "cases.sqlite3"
    apply_schema(create_sqlite_engine(database), schema_sql_path().read_text(encoding="utf-8"))
    registry = validate_modules(load_modules_file(tmp_path), app_env="test")
    assert registry.adapter_name("cross_case") == ("sql_context" if on else "null")
    clock = FakeClock(T0)
    ids = UlidIdGenerator()
    adapters = build_adapters(registry, clock=clock, ids=ids, data_dir=tmp_path, database=database)
    settings = Settings(app_env="test", test_seller_email=SELLER_CONTACT)
    return _build_runtime(tmp_path, database, settings, clock, ids, adapters, registry), clock


def outcomes(tmp_path: Path, on: bool) -> list[tuple[str, str | None]]:
    rt, clock = runtime(tmp_path, on)
    found = []
    for n, name in enumerate(FIXTURES):
        shipment = fixture(name).shipment
        body = RaiseDispute(
            source_event_id=f"form-{n}",
            shipment_ref=shipment.ref,
            claim_ref=shipment.claim_ref,
            seller_ref=shipment.seller_id,
            seller_contact=SELLER_CONTACT,
            raised_at=clock.now(),
        )
        event = InboundEvent(
            type=InboundEventType.DISPUTE_RAISED,
            source=body.source,
            source_event_id=body.source_event_id,
            occurred_at=body.raised_at,
            actor=Actor(kind=ActorKind.EXTERNAL, id=body.source),
            payload=InboundPayload(),
        )
        result = rt.submit_dispute(event, body)
        found.append((str(result.state), result.review_reason))
        clock.advance(timedelta(hours=1))
    report = rt.runner.tick()
    assert report.failures == []
    assert report.jobs_run == (["cross_case"] if on else [])
    assert (rt.runner.line.patterns is None) is (not on)
    return found


def test_every_single_case_ends_the_same_with_the_module_on_and_off(tmp_path: Path) -> None:
    with_module = outcomes(tmp_path / "on", on=True)
    without = outcomes(tmp_path / "off", on=False)
    assert with_module == without


@pytest.mark.parametrize("on", [True, False])
def test_the_running_system_starts_and_ticks_either_way(tmp_path: Path, on: bool) -> None:
    rt, _ = runtime(tmp_path, on)
    assert rt.runner.tick().failures == []
