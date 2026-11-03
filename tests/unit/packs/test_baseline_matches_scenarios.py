"""Card P2-08: the baseline agrees with `eval-scenarios.yaml`, scenario by scenario.

`eval-scenarios.yaml` is read **at test time**. That is the whole point: the catalogue
records what the baseline is expected to do on every fixture, and until now the only thing
checking that was a person reading both during the Stage A4 review. This turns it into a
test, so a change to the decision table that quietly moves a scenario fails in CI rather
than in a Phase 6 eval run.

It also exercises the pack end to end on real fixtures — gather a round, assess it, build a
briefing, run the table — which is why a broken reader in `assess` shows up here as a
scenario mismatch rather than as nothing at all.

`baseline: same` means the baseline is expected to produce whatever the scenario's first
expected action is; `baseline: n/a` means INVESTIGATING is never reached, so there is no
baseline proposal to check and the scenario is skipped. Scenarios with injected `faults` are
skipped too: the fault changes what the round finds, which is P2-05's subject, not this one.

Every scenario is checked on its **first** gather round, which is the round the catalogue's
`baseline:` entry describes. A later round carries a reply, and B5 escalates every reply, so
checking those would test the same row over and over.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import CaseState, ProposalAction, Provenance
from dispute_agent.kernel.domain.evidence import EvidenceBundle, EvidenceItem
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import CaseId, EvidenceItemId, ProposalId
from dispute_agent.kernel.domain.reasoning import CaseBriefing, ContactStatus
from dispute_agent.kernel.orchestrator.evidence import gather
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

ROOT = Path(__file__).resolve().parents[3]
SCENARIOS = yaml.safe_load(
    (ROOT / "docs" / "spec" / "eval-scenarios.yaml").read_text(encoding="utf-8")
)["scenarios"]

PACK = WeightDisputePack()
NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
CASE = CaseId("case_scenarios")

ACTIONS = {a.value for a in ProposalAction}


def facts_for(name: str) -> CaseFacts:
    built = fixture(name)
    return CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=datetime(2026, 2, 1, tzinfo=UTC),
        pack_facts={STATUS_KEY: built.shipment.status},
    )


def briefing_for(name: str, *, gather_round: int = 1) -> CaseBriefing:
    """One real gather round on the fixture, assessed, as the briefing the table sees."""
    facts = facts_for(name)
    source = SyntheticShipmentSource(fixture=fixture(name))
    outcome = gather(
        case_id=CASE,
        facts=facts,
        requirements=PACK.evidence_plan(facts),
        sources={"evidence": source},
        now=NOW,
    )
    bundle = EvidenceBundle(
        case_id=CASE,
        gather_round=gather_round,
        items=tuple(
            EvidenceItem(
                id=EvidenceItemId(f"evi_{n}"),
                case_id=CASE,
                gather_round=gather_round,
                provenance=Provenance.SYSTEM,
                result=result,
                content_hash=payload_hash(result.model_dump(mode="json")),
            )
            for n, result in enumerate(outcome.results)
        ),
    )
    assessment = PACK.assess(CASE, gather_round, bundle)
    return CaseBriefing(
        case_id=CASE,
        state=CaseState.INVESTIGATING,
        facts=facts,
        evidence=bundle,
        assessment=assessment,
        allowed_actions=tuple(PACK.allowed_actions(CaseState.INVESTIGATING, assessment)),
        contact=ContactStatus(
            reply_in_round=False, clarifications_sent=0, remeasurement_outstanding=False
        ),
        briefing_hash=payload_hash(bundle.model_dump(mode="json")),
    )


def expected_baseline(scenario: dict[str, Any]) -> str | None:
    """The action the catalogue expects, or None when it does not say."""
    expect = scenario.get("expect") or {}
    declared = expect.get("baseline")
    if not isinstance(declared, str):
        return None
    declared = declared.split("#")[0].strip()
    if declared in ACTIONS:
        return declared
    if declared != "same":
        return None  # "n/a": INVESTIGATING is never reached.

    actions = expect.get("actions")
    if isinstance(actions, list) and actions and isinstance(actions[0], str):
        first = actions[0].split("(")[0].strip()
        return first if first in ACTIONS else None
    return None


#: Scenarios whose declared baseline cannot be reproduced from a first gather round, with
#: the reason. Each is a real gap rather than a disagreement, and naming them here is what
#: stops the list quietly growing: a scenario that stops matching has to be argued into it.
NOT_FROM_A_FIRST_ROUND = {
    "S14b": "B2 needs an input screen flag, and there is no InputScreen adapter until Phase 4",
    "S24": "a repeat dispute: B3 is blocked by a verified credit on an earlier case",
    "S43a": "a reopened credited case: B3 is blocked by the verified credit",
    "S26": "the finding arrives days after the first round, on a reopened case",
}


def starts_fresh(scenario: dict[str, Any]) -> bool:
    """A case that begins with its own dispute, not one continuing another scenario."""
    script = scenario.get("script")
    return isinstance(script, list) and bool(script) and script[0] == "dispute_raised@0h"


CHECKABLE = [
    s
    for s in SCENARIOS
    if isinstance(s.get("fixture"), str)
    and expected_baseline(s) is not None
    and not s.get("faults")
    and starts_fresh(s)
    and s.get("id") not in NOT_FROM_A_FIRST_ROUND
]


class TestTheCatalogueIsCheckable:
    def test_there_is_something_to_check(self) -> None:
        """If the parser stopped finding scenarios, every test below would pass by being
        skipped. This is what stops that."""
        assert len(CHECKABLE) >= 5, f"only {len(CHECKABLE)} first-round scenarios parsed"

    def test_every_excluded_scenario_still_exists(self) -> None:
        """So the exclusion list cannot outlive the scenarios it excuses and quietly hide a
        rename."""
        ids = {s.get("id") for s in SCENARIOS}
        assert set(NOT_FROM_A_FIRST_ROUND) <= ids

    def test_every_scenario_names_a_fixture_that_exists(self) -> None:
        for scenario in CHECKABLE:
            fixture(scenario["fixture"])


@pytest.mark.parametrize("scenario", CHECKABLE, ids=lambda s: str(s["id"]))
class TestTheBaselineMatchesTheCatalogue:
    def test_the_baseline_produces_the_documented_action(self, scenario: dict[str, Any]) -> None:
        name = scenario["fixture"]
        briefing = briefing_for(name)
        row = PACK.baseline_row(briefing)
        expected = expected_baseline(scenario)
        proposal = PACK.baseline_proposal(
            briefing,
            proposal_id=ProposalId("prp_scenario"),
            now=NOW,
        )
        assert proposal.action.value == expected, (
            f"{scenario['id']} ({name}): catalogue says {expected}, "
            f"row {row} produced {proposal.action.value}"
        )


class TestTheInstructiveOpposites:
    """The two the spec calls out by name, pinned so a reader can check them by hand."""

    def test_the_kurta_escalates_because_of_its_image(self) -> None:
        """S02-S04, S08: `kurta_box_larger` carries an image evidence item, so B5 fires in
        the first round and the baseline does not judge the picture."""
        briefing = briefing_for("kurta_box_larger")
        assert PACK.baseline_row(briefing) == "B5"

    def test_scanner_fault_asks_the_seller_because_it_has_none(self) -> None:
        """S06: no image, no history, no reply in round 1, so B7 fires and the baseline
        matches the AI's first action. This is where the AI has nothing to beat."""
        briefing = briefing_for("scanner_fault")
        assert PACK.baseline_row(briefing) == "B7"

    def test_a_wrong_slab_credits_without_judgement(self) -> None:
        """B3 above B5: the money was proven by arithmetic on structured values."""
        briefing = briefing_for("calc_error_wrong_slab")
        assert PACK.baseline_row(briefing) == "B3"

    def test_two_errors_escalate(self) -> None:
        """S38, R14: two proofs of overcharge are two different numbers."""
        assert PACK.baseline_row(briefing_for("multi_error")) == "B4"
