"""Deterministic clustering (P7-03). Code finds clusters; a model only ever describes them.

A pure function of explicit inputs, so it is tested without a database. The rule:

1. Take the cases opened in the window (`patterns.window_days`, default 14).
2. Group them by each key: scanner, hub, SKU, seller.
3. A group with at least `patterns.min_related_cases` distinct cases (default 3) is a cluster.
4. One explanation per set of cases. A cluster whose cases are a strict subset of another
   cluster's is dropped: the bigger group already covers them, and three hubs inside one
   SKU's twelve disputes are the SKU's pattern, not three more. Among clusters with
   *identical* cases the narrowest physical cause wins, in the order scanner, hub, SKU,
   seller: five disputes through one scanner at one hub are a scanner pattern.

Known limit (ADR 0036): a narrow cause inside a much broader group is reported as the broad
group. That only ever widens who is briefed with caution; it never narrows it.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

#: Narrowest physical cause first. Also the tie-break order for identical case sets.
DIMENSIONS: tuple[str, ...] = ("scanner", "hub", "sku", "seller")


@dataclass(frozen=True)
class Thresholds:
    """`config/policy.yaml` `patterns` (config.md). Recorded on every pattern it produces."""

    min_related_cases: int = 3
    window_days: int = 14

    def as_dict(self) -> dict[str, int]:
        return {"min_related_cases": self.min_related_cases, "window_days": self.window_days}


@dataclass(frozen=True)
class KeyedCase:
    case_id: str
    opened_at: datetime
    keys: Mapping[str, str]


@dataclass(frozen=True)
class Cluster:
    dimension: str
    value: str
    case_ids: tuple[str, ...]
    first_seen_at: datetime
    last_seen_at: datetime

    @property
    def size(self) -> int:
        return len(self.case_ids)


def find_clusters(
    cases: Sequence[KeyedCase], thresholds: Thresholds, now: datetime
) -> list[Cluster]:
    start = now - timedelta(days=thresholds.window_days)
    recent = [c for c in cases if start <= c.opened_at <= now]

    candidates: list[Cluster] = []
    for dimension in DIMENSIONS:
        groups: dict[str, dict[str, datetime]] = defaultdict(dict)
        for case in recent:
            value = case.keys.get(dimension)
            if value:
                groups[value][case.case_id] = case.opened_at
        for value in sorted(groups):
            members = groups[value]
            if len(members) < thresholds.min_related_cases:
                continue
            candidates.append(
                Cluster(
                    dimension=dimension,
                    value=value,
                    case_ids=tuple(sorted(members)),
                    first_seen_at=min(members.values()),
                    last_seen_at=max(members.values()),
                )
            )
    return _one_explanation_each(candidates)


def _one_explanation_each(candidates: list[Cluster]) -> list[Cluster]:
    kept: list[Cluster] = []
    seen_sets: set[frozenset[str]] = set()
    sets = [frozenset(c.case_ids) for c in candidates]
    for cluster, members in zip(candidates, sets, strict=True):
        if any(members < other for other in sets):
            continue  # covered by a bigger group
        if members in seen_sets:
            continue  # an identical set, already explained by a narrower cause
        seen_sets.add(members)
        kept.append(cluster)
    return kept


def summary_for(cluster: Cluster) -> str:
    """Code-written, so it states only what was counted. Never a cause, never a fault."""
    days = max(1, (cluster.last_seen_at - cluster.first_seen_at).days + 1)
    label = {"scanner": "scanner", "hub": "hub", "sku": "SKU", "seller": "seller"}[
        cluster.dimension
    ]
    return (
        f"{cluster.size} disputes opened within {days} days share {label} {cluster.value}. "
        "This is a hypothesis about a possible common cause, not a finding: no case is "
        "decided on it."
    )
