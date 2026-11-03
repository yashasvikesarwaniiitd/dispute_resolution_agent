"""Card P2-08: the `rules_only` reasoner adapter.

Thin on purpose — it asks the pack's table and returns the answer. The tests that matter are
the two about what it is *not* allowed to be: it never reaches a model (I14 holds by there
being nothing to route) and it never writes (I1 holds by `propose` taking a briefing and
returning a proposal, with no store, channel or executor in the signature).

The third is that it is reproducible. ADR 0024 makes the baseline the bar the AI is measured
against, and a bar that moves between runs measures nothing.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any

from dispute_agent.infra.clock import FakeClock
from dispute_agent.infra.ids import FakeIdGenerator
from dispute_agent.kernel.domain.enums import CaseState, Confidence, ProposalAction, Provenance
from dispute_agent.kernel.domain.evidence import EvidenceBundle, EvidenceItem
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import CaseId, EvidenceItemId
from dispute_agent.kernel.domain.reasoning import CaseBriefing, ContactStatus
from dispute_agent.kernel.orchestrator.evidence import gather
from dispute_agent.modules.evidence.synthetic_shipments.fixtures import fixture
from dispute_agent.modules.evidence.synthetic_shipments.source import SyntheticShipmentSource
from dispute_agent.modules.reasoning.rules_only.reasoner import RulesOnlyReasoner
from dispute_agent.packs.weight_dispute.eligibility import STATUS_KEY
from dispute_agent.packs.weight_dispute.pack import WeightDisputePack

NOW = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)
CASE = CaseId("case_rules_only")
PACK = WeightDisputePack()


def briefing_for(name: str) -> CaseBriefing:
    from dispute_agent.kernel.domain.case import CaseFacts

    built = fixture(name)
    facts = CaseFacts(
        shipment_ref=built.shipment.ref,
        claim_ref=built.shipment.claim_ref,
        seller_ref=built.shipment.seller_id,
        seller_contact=built.seller_contact,
        raised_at=NOW,
        pack_facts={STATUS_KEY: built.shipment.status},
    )
    outcome = gather(
        case_id=CASE,
        facts=facts,
        requirements=PACK.evidence_plan(facts),
        sources={"evidence": SyntheticShipmentSource(fixture=built, clock=FakeClock(NOW))},
        now=NOW,
    )
    bundle = EvidenceBundle(
        case_id=CASE,
        gather_round=1,
        items=tuple(
            EvidenceItem(
                id=EvidenceItemId(f"evi_{n}"),
                case_id=CASE,
                gather_round=1,
                provenance=Provenance.SYSTEM,
                result=result,
                content_hash=payload_hash(result.model_dump(mode="json")),
            )
            for n, result in enumerate(outcome.results)
        ),
    )
    assessment = PACK.assess(CASE, 1, bundle)
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


def reasoner(**kwargs: Any) -> RulesOnlyReasoner:
    return RulesOnlyReasoner(pack=PACK, ids=FakeIdGenerator(), clock=FakeClock(NOW), **kwargs)


class TestItProposes:
    def test_it_returns_the_table_s_answer(self) -> None:
        proposal = reasoner().propose(briefing_for("calc_error_wrong_slab"))
        assert proposal.action is ProposalAction.PROPOSE_CREDIT
        assert proposal.action_parameters.reason_code == "calculation_error"

    def test_it_names_itself_rules_only(self) -> None:
        assert reasoner().propose(briefing_for("kurta_box_larger")).reasoner == "rules_only"

    def test_confidence_is_high_because_the_table_is_deterministic(self) -> None:
        assert reasoner().propose(briefing_for("kurta_box_larger")).confidence is Confidence.HIGH

    def test_the_fallback_flag_says_whether_a_model_was_meant_to_run(self) -> None:
        """Same table either way. What differs is whether this stood in for something."""
        assert reasoner().propose(briefing_for("kurta_box_larger")).fallback_used is False
        stand_in = reasoner(fallback_used=True)
        assert stand_in.propose(briefing_for("kurta_box_larger")).fallback_used is True


class TestItCannotReachAModel:
    def test_no_prompt_or_model_is_recorded_on_the_proposal(self) -> None:
        """I14: a proposal claiming a prompt hash it never used would make the audit trail
        a lie, and here there is genuinely nothing to record."""
        proposal = reasoner().propose(briefing_for("kurta_box_larger"))
        assert proposal.prompt_name is None
        assert proposal.prompt_version is None
        assert proposal.prompt_hash is None
        assert proposal.model_id is None
        assert proposal.model_route is None

    def test_it_holds_no_gateway(self) -> None:
        """Structural: there is no port on this adapter that could reach a model."""
        held = set(vars(reasoner()))
        assert "gateway" not in held
        assert "model_gateway" not in held


class TestItCannotWrite:
    def test_propose_takes_a_briefing_and_returns_a_proposal(self) -> None:
        """I1, read off the signature. A store, a channel or an executor appearing here
        would mean the reasoner had been handed something it can write with."""
        signature = inspect.signature(RulesOnlyReasoner.propose)
        assert set(signature.parameters) == {"self", "briefing"}

    def test_it_never_reads_patterns(self) -> None:
        """I7 holds trivially on this path, and the proposal says so rather than leaving a
        reader to infer it."""
        influence = reasoner().propose(briefing_for("kurta_box_larger")).pattern_influence
        assert influence.considered is False
        assert influence.changed_recommendation is False


class TestItIsReproducible:
    def test_the_same_briefing_gives_the_same_proposal(self) -> None:
        """ADR 0024: a bar that moves between runs measures nothing."""
        briefing = briefing_for("scanner_fault")
        assert reasoner().propose(briefing) == reasoner().propose(briefing)

    def test_it_does_not_read_the_wall_clock(self) -> None:
        """The clock is injected, so a proposal built in a test carries the test's time."""
        assert reasoner().propose(briefing_for("kurta_box_larger")).created_at == NOW
