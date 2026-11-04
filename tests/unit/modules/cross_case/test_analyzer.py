"""P7-03: the analyzer job over a real database. Reads cases; writes only pattern rows."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.modules.cross_case.analyzer import PatternAnalyzer, Thresholds
from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository
from dispute_agent.modules.evidence.synthetic_shipments.library import SyntheticLibrarySource
from tests.support.patterns import T0, add_case, new_engine

if TYPE_CHECKING:
    from sqlalchemy import Engine

DAY = timedelta(days=1)


def world(name: str, count: int, spacing: timedelta = 2 * DAY) -> tuple[PatternAnalyzer, Engine]:
    from dispute_agent.modules.evidence.synthetic_shipments.fixtures import cluster_members, fixture

    engine = new_engine()
    for n, member in enumerate(cluster_members(fixture(name))[:count]):
        add_case(
            engine,
            f"case_{n:02d}",
            claim=member.claim_ref,
            shipment=member.ref,
            seller=member.seller_id,
            opened_at=T0 + n * spacing,
        )
    analyzer = PatternAnalyzer(
        repository=PatternRepository(engine),
        keys=SyntheticLibrarySource().keys_for,
        ids=FakeIdGenerator(),
        thresholds=Thresholds(),
    )
    return analyzer, engine


def test_five_disputes_through_one_scanner_make_one_pattern() -> None:
    analyzer, _ = world("hub_scanner_cluster", 5)
    report = analyzer.run(T0 + 11 * DAY)
    assert len(report.created) == 1
    [pattern] = analyzer.repository.all()
    assert (pattern.dimension, pattern.match_value, pattern.status) == (
        "scanner",
        "SCN-03-2",
        "UNCONFIRMED",
    )
    assert pattern.thresholds == {"min_related_cases": 3, "window_days": 14}
    assert len(analyzer.repository.linked(pattern.id)) == 5


def test_a_rerun_refreshes_rather_than_duplicates() -> None:
    analyzer, _ = world("hub_scanner_cluster", 5)
    analyzer.run(T0 + 11 * DAY)
    report = analyzer.run(T0 + 12 * DAY)
    assert report.created == [] and len(report.refreshed) == 1
    assert len(analyzer.repository.all()) == 1


def test_a_dismissed_pattern_stays_dismissed_on_a_rerun() -> None:
    analyzer, _ = world("hub_scanner_cluster", 5)
    analyzer.run(T0 + 11 * DAY)
    [pattern] = analyzer.repository.all()
    analyzer.repository.set_status(pattern.id, "DISMISSED", "ops:no_fault_found", T0 + 11 * DAY)
    analyzer.run(T0 + 12 * DAY)
    assert analyzer.repository.get(pattern.id).status == "DISMISSED"


def test_two_disputes_make_no_pattern() -> None:
    analyzer, _ = world("hub_scanner_cluster", 2)
    assert analyzer.run(T0 + 11 * DAY).created == []
    assert analyzer.repository.all() == []


def test_a_shipment_without_keys_is_reported_not_guessed() -> None:
    analyzer, engine = world("hub_scanner_cluster", 3)
    add_case(engine, "case_unknown", shipment="SHP-NOWHERE", opened_at=T0 + DAY)
    report = analyzer.run(T0 + 11 * DAY)
    assert report.unreadable == ["case_unknown"]


def test_seasonal_sku_is_one_pattern_on_the_sku() -> None:
    analyzer, _ = world("seasonal_bulky_sku", 12, spacing=timedelta(hours=12))
    analyzer.run(T0 + 7 * DAY)
    assert [(p.dimension, p.match_value) for p in analyzer.repository.all()] == [
        ("sku", "SKU-FEST-01")
    ]
