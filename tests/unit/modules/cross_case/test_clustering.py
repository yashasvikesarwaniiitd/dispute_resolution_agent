"""P7-03: clustering is code, deterministic, and finds what the fixtures describe."""

from __future__ import annotations

from datetime import timedelta

from dispute_agent.modules.cross_case.analyzer.clustering import (
    KeyedCase,
    Thresholds,
    find_clusters,
    summary_for,
)
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import cluster_members, fixture
from tests.support.patterns import T0

DAY = timedelta(days=1)


def keyed(name: str, count: int | None = None, spacing: timedelta = 2 * DAY) -> list[KeyedCase]:
    members = cluster_members(fixture(name))[: count or None]
    return [
        KeyedCase(
            case_id=f"case_{m.ref}",
            opened_at=T0 + n * spacing,
            keys={"scanner": m.scanner_id, "hub": m.hub_id, "sku": m.sku_id, "seller": m.seller_id},
        )
        for n, m in enumerate(members)
    ]


def at_end(cases: list[KeyedCase]) -> object:
    return max(c.opened_at for c in cases) + DAY


def test_hub_scanner_cluster_yields_one_cluster_on_the_scanner() -> None:
    cases = keyed("hub_scanner_cluster", 5)
    clusters = find_clusters(cases, Thresholds(), at_end(cases))  # type: ignore[arg-type]
    assert [(c.dimension, c.value, c.size) for c in clusters] == [("scanner", "SCN-03-2", 5)]


def test_below_the_threshold_there_is_no_cluster() -> None:
    cases = keyed("hub_scanner_cluster", 2)
    assert find_clusters(cases, Thresholds(), at_end(cases)) == []  # type: ignore[arg-type]


def test_outside_the_window_there_is_no_cluster() -> None:
    cases = keyed("hub_scanner_cluster", 5, spacing=10 * DAY)  # 40 days end to end
    clusters = find_clusters(cases, Thresholds(), at_end(cases))  # type: ignore[arg-type]
    assert clusters == []


def test_seasonal_bulky_sku_clusters_on_the_sku_not_on_four_hubs() -> None:
    cases = keyed("seasonal_bulky_sku", spacing=timedelta(hours=12))
    clusters = find_clusters(cases, Thresholds(), at_end(cases))  # type: ignore[arg-type]
    assert [(c.dimension, c.value, c.size) for c in clusters] == [("sku", "SKU-FEST-01", 12)]


def test_the_thresholds_come_from_config() -> None:
    cases = keyed("hub_scanner_cluster", 5)
    strict = Thresholds(min_related_cases=6, window_days=14)
    assert find_clusters(cases, strict, at_end(cases)) == []  # type: ignore[arg-type]


def test_the_summary_states_counts_never_a_cause() -> None:
    cases = keyed("hub_scanner_cluster", 5)
    [cluster] = find_clusters(cases, Thresholds(), at_end(cases))  # type: ignore[arg-type]
    text = summary_for(cluster).lower()
    assert "5 disputes" in text and "hypothesis" in text
    for word in ("fault", "fraud", "guilty", "credit", "refund"):
        assert word not in text.replace("not a finding", "")
