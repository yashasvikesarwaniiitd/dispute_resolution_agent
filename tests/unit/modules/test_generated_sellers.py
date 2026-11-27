"""ADR 0072: the generated sellers sit beside the approved ones and can never move them.

- The frozen environment (SELLER_CATALOGUE=approved) reproduces the 84 approved comparisons
  exactly as snapshotted, and so does the full catalogue: a comparison only ever reads its
  own seller's shipments, so seven more sellers change none of them.
- The generator is deterministic: regenerating writes the committed bytes.
- The generated world has the size and the variations the investigation cases need, and keeps
  the planted labels out of everything the agent is served.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from dispute_agent.modules.evidence.synthetic_shipments.catalogue import catalogue

ROOT = Path(__file__).resolve().parents[3]
SNAPSHOT = ROOT / "tests" / "fixtures" / "frozen" / "approved_comparisons.json"
APPROVED = {"SELL-0101", "SELL-0102", "SELL-0103"}


def approved_comparisons() -> dict[str, object]:
    return {
        ref: json.loads(json.dumps(f.comparable_history, sort_keys=True, default=str))
        for ref, f in sorted(catalogue().by_ref.items())
        if f.shipment.seller_id in APPROVED
    }


@pytest.mark.usefixtures("approved_catalogue")
def test_the_frozen_environment_reproduces_every_approved_comparison() -> None:
    assert {s.seller.seller_id for s in catalogue().sellers} == APPROVED
    assert approved_comparisons() == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_generated_sellers_change_no_approved_comparison() -> None:
    assert len(catalogue().sellers) == 10
    assert approved_comparisons() == json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def test_the_generator_reproduces_the_committed_files() -> None:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate_sellers.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_the_world_is_small_diverse_and_shared() -> None:
    rows = [(s.seller.seller_id, r) for s in catalogue().sellers for r in s.shipments]
    assert 300 <= len(rows) <= 500
    sellers_on: dict[str, set[str]] = {}
    for seller, row in rows:
        sellers_on.setdefault(row.scanner_id, set()).add(seller)
    assert max(len(s) for s in sellers_on.values()) >= 3  # scanners are shared across sellers
    statuses = {r.measurement_status for _, r in rows}
    assert statuses == {"trusted", "disputed", "corrected"}
    assert {r.quantity for _, r in rows} >= {1, 2, 3, 4, 5}
    packagings_per_sku: dict[str, set[str]] = {}
    for _, row in rows:
        packagings_per_sku.setdefault(row.sku_id, set()).add(row.packaging_id)
    assert sum(1 for p in packagings_per_sku.values() if len(p) > 1) >= 3


def test_every_investigation_case_has_its_dispute_row() -> None:
    import yaml

    cases = yaml.safe_load((ROOT / "evals" / "investigation" / "cases_v1.yaml").read_text("utf-8"))
    by_name = catalogue().by_name
    for case in cases["cases"]:
        built = by_name.get(case["fixture"])
        assert built is not None, case["id"]
        assert built.shipment.ref == case["shipment"], case["id"]


def test_planted_labels_never_reach_a_served_fixture() -> None:
    """The variation label (demo_label) and the manifest stay out of what the agent is served."""
    for built in catalogue().by_ref.values():
        served = json.dumps(
            {"shipment": str(built.shipment), "history": built.comparable_history}, default=str
        )
        assert "drift" not in served and "variation" not in served
