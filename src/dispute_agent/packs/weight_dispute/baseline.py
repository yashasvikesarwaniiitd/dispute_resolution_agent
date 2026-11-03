"""The rules-only baseline (agent-design.md section 5, kernel-interfaces.md 15.2).

Nine rows, first match wins, total by construction. It is two things at once: the
`rules_only` reasoning adapter, and what runs when the model is unavailable. ADR 0024 makes
it the bar the AI has to beat rather than a degraded mode — so a change here moves the
measuring stick, not just a fallback.

**The property this table exists to have: the baseline never judges free text or images.**
B5 is where that lives. Once the deterministic results give no single definite error, a
seller reply, an image or a conflicting history escalates before any row that would decide
the case on its merits. That is why most of the scenario catalogue expects
`escalate_to_human` from the baseline, and why the AI is measured on beating it exactly
there.

Two orderings are load-bearing and neither is an accident.

**B3 sits above B5.** A code-proven credit needs no judgement, so it fires even when the
round carries a reply or an image: the money was proven by arithmetic on structured values,
and nothing in a sentence changes it. Swap these two and the baseline silently stops
crediting on roughly half the fixtures.

**B6 sits above B8.** A case inside tolerance that is still waiting on our own hub waits
rather than upholding. Upholding while a remeasurement we asked for is outstanding decides
the case on evidence we have not seen yet, which is the fail-safe rule in CLAUDE.md section
6 applied to the one place it is tempting to skip.

The baseline carries no review reason of its own. It returns `escalate_to_human` and the
orchestrator maps that to `proposal_escalated` (orchestrator.md section 3.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import (
    Confidence,
    EvidenceStatus,
    FindingKind,
    ProposalAction,
    Provenance,
)
from dispute_agent.kernel.domain.reasoning import (
    ActionParameters,
    Finding,
    PatternInfluence,
    Proposal,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.evidence import EvidenceItem
    from dispute_agent.kernel.domain.ids import ProposalId
    from dispute_agent.kernel.domain.reasoning import CaseBriefing

#: Evidence keys the table reads by name.
IMAGE_KEY = "shipment.image_description"
HISTORY_KEY = "shipment.history"
DECLARED_KEY = "shipment.declared_dims"
MEASURED_KEY = "shipment.measured_dims"

#: Reply intents B2 treats as a reason to stop. A reply that tries to instruct us, or that
#: withdraws the dispute, is a decision for a person either way.
STOP_INTENTS = frozenset({"contains_instruction_attempt", "withdraws_dispute"})

#: B7's wording, verbatim from agent-design.md section 5. Kept here rather than in a template
#: because the baseline's output must be identical every time it runs.
B7_ITEMS = (
    "Photo of the packed box beside a tape measure",
    "Packaging dimensions at dispatch",
)


@dataclass(frozen=True)
class BaselineDecision:
    """Which row fired, and what it decided.

    The row is part of the output on purpose. "The baseline escalated" is not reviewable;
    "B5 fired because the round carries an image item" is, and it is what the finding on the
    proposal says.
    """

    row: str
    action: ProposalAction
    because: str
    evidence_refs: tuple[str, ...] = ()
    requested_items: tuple[str, ...] = ()
    reason_code: str | None = None
    #: Every row that was tested and did not match, in order. Only for explaining a result.
    skipped: tuple[str, ...] = field(default_factory=tuple)


def decide(
    briefing: CaseBriefing,
    *,
    required_keys: Collection[str],
    tolerance_pct: int = 5,
) -> BaselineDecision:
    """Walk B1-B9 in order and return the first row that matches.

    `required_keys` comes from the pack's own `evidence_plan`: an `EvidenceItem` records
    what was asked for and what came back, but not whether the pack could do without it, and
    B1 turns entirely on that distinction.
    """
    items = briefing.evidence.items
    skipped: list[str] = []

    def no(row: str) -> None:
        skipped.append(row)

    # B1 - we do not know enough to decide anything.
    gaps = _required_gaps(items, required_keys)
    if gaps:
        return BaselineDecision(
            "B1",
            ProposalAction.ESCALATE_TO_HUMAN,
            f"required evidence not available: {', '.join(gaps)}",
            evidence_refs=gaps,
            skipped=tuple(skipped),
        )
    no("B1")

    # B2 - something tried to steer us, or a reply could not be read (I5, I15).
    tampering = _tampering(briefing, items)
    if tampering:
        return BaselineDecision(
            "B2",
            ProposalAction.ESCALATE_TO_HUMAN,
            tampering,
            skipped=tuple(skipped),
        )
    no("B2")

    errors = briefing.assessment.definite_errors

    # B3 - above B5 on purpose: code proved the money, so no judgement is needed.
    if len(errors) == 1 and ProposalAction.PROPOSE_CREDIT in briefing.allowed_actions:
        return BaselineDecision(
            "B3",
            ProposalAction.PROPOSE_CREDIT,
            f"one definite error proved by code: {errors[0]}",
            evidence_refs=_fact_ids(briefing),
            reason_code=errors[0],
            skipped=tuple(skipped),
        )
    no("B3")

    # B4 - two proofs of overcharge are two different numbers (R14).
    if len(errors) > 1:
        return BaselineDecision(
            "B4",
            ProposalAction.ESCALATE_TO_HUMAN,
            f"{len(errors)} definite errors, so there is no single amount to credit",
            skipped=tuple(skipped),
        )
    no("B4")

    # B5 - the line the baseline does not cross.
    judgement = _needs_judgement(briefing, items)
    if judgement:
        return BaselineDecision(
            "B5",
            ProposalAction.ESCALATE_TO_HUMAN,
            judgement,
            skipped=tuple(skipped),
        )
    no("B5")

    # B6 - above B8: do not uphold while our own hub is still measuring.
    if briefing.contact.remeasurement_outstanding:
        return BaselineDecision(
            "B6",
            ProposalAction.WAIT,
            "a remeasurement we asked for is outstanding and nothing new has arrived",
            skipped=tuple(skipped),
        )
    no("B6")

    oversize = _oversize_beyond_tolerance(items, tolerance_pct)
    first_round = briefing.evidence.gather_round <= 1

    # B7 - the charge is right on what was measured, but the box measured bigger than it was
    # declared. Ask the seller for the one thing that would settle it.
    if not errors and oversize is True and first_round:
        return BaselineDecision(
            "B7",
            ProposalAction.REQUEST_SELLER_EVIDENCE,
            f"measured size exceeds declared by more than {tolerance_pct}%",
            evidence_refs=_refs(items, (DECLARED_KEY, MEASURED_KEY)),
            requested_items=B7_ITEMS,
            skipped=tuple(skipped),
        )
    no("B7")

    # B8 - the charge follows the rules and the box is the size it was said to be.
    if (
        not errors
        and oversize is False
        and ProposalAction.UPHOLD_CHARGE in briefing.allowed_actions
    ):
        return BaselineDecision(
            "B8",
            ProposalAction.UPHOLD_CHARGE,
            f"charge follows the rate card and measured size is within {tolerance_pct}% of declared",
            evidence_refs=_refs(items, (DECLARED_KEY, MEASURED_KEY)),
            skipped=tuple(skipped),
        )
    no("B8")

    # B9 - the catch-all that makes the table total. Reaching it is not a failure; it is the
    # table saying it has nothing to offer, which is the honest answer.
    return BaselineDecision(
        "B9",
        ProposalAction.ESCALATE_TO_HUMAN,
        "no baseline row applies to this case",
        skipped=tuple(skipped),
    )


def baseline_proposal(
    briefing: CaseBriefing,
    *,
    proposal_id: ProposalId,
    now: datetime,
    required_keys: Collection[str],
    tolerance_pct: int = 5,
    fallback_used: bool = False,
) -> Proposal:
    """The decision, as the `Proposal` the orchestrator expects (section 15.2).

    `confidence` is always `high`: the table is deterministic, so there is nothing to be
    uncertain about, and R22 (low confidence adds caution) therefore never fires on a
    baseline proposal. `pattern_influence` is all false because the baseline never reads
    `pattern_context` — which is also I7 holding trivially on this path.
    """
    decision = decide(briefing, required_keys=required_keys, tolerance_pct=tolerance_pct)
    return Proposal(
        id=proposal_id,
        case_id=briefing.case_id,
        gather_round=briefing.evidence.gather_round,
        action=decision.action,
        confidence=Confidence.HIGH,
        rationale=f"{decision.row}: {decision.because}"[:800],
        findings=(
            Finding(
                kind=FindingKind.VERIFIED_FACT,
                statement=f"{decision.row}: {decision.because}"[:400],
                evidence_refs=decision.evidence_refs,
            ),
        ),
        action_parameters=ActionParameters(
            reason_code=decision.reason_code,
            requested_items=decision.requested_items,
        ),
        pattern_influence=PatternInfluence(considered=False, changed_recommendation=False),
        reasoner="rules_only",
        fallback_used=fallback_used,
        briefing_hash=briefing.briefing_hash,
        created_at=now,
    )


# -- the conditions, one function each -----------------------------------------------------


def _required_gaps(
    items: Sequence[EvidenceItem], required_keys: Collection[str]
) -> tuple[str, ...]:
    """B1: required keys that failed, are missing, or never came back at all.

    A key with no item is treated exactly like a MISSING one. The pack asked for it and has
    no answer; which of those two shapes the absence took is not a difference the baseline
    can act on.
    """
    by_key = {item.result.requirement_key: item for item in items}
    gaps = []
    for key in required_keys:
        item = by_key.get(key)
        if item is None or item.result.status is not EvidenceStatus.FOUND:
            gaps.append(key)
    return tuple(sorted(gaps))


def _tampering(briefing: CaseBriefing, items: Sequence[EvidenceItem]) -> str:
    """B2: a screen flag, a stop intent, or a reply nobody could read.

    All three are the same situation from the baseline's point of view: the round contains
    something that may be trying to steer the outcome, and a deterministic table is the
    worst possible thing to settle that with (I15 — a flag may only add caution).
    """
    for screen in briefing.screen_results:
        if screen.blocked:
            return f"input screen blocked a {screen.kind.value.lower()}"
        if screen.flags:
            return f"input screen flagged a {screen.kind.value.lower()}: {', '.join(screen.flags)}"

    readings = [item for item in items if item.provenance is Provenance.AI_DERIVED]
    for item in readings:
        if item.result.status is not EvidenceStatus.FOUND:
            return "a reply in this round could not be read"
        intent = (item.result.value or {}).get("intent")
        if isinstance(intent, str) and intent in STOP_INTENTS:
            return f"the reply reads as {intent}"

    # No clause here for "a reply arrived and nothing interpreted it". B2 is about a reading
    # that was attempted and failed; a reply with no reading at all belongs to B5, which
    # escalates every reply the baseline cannot judge. An earlier draft had this row claim
    # it, which looked harmless and was not: until Phase 4 there is no interpreter, so every
    # reply would have been reported as tampering and B5 would never have fired at all.
    return ""


def _needs_judgement(briefing: CaseBriefing, items: Sequence[EvidenceItem]) -> str:
    """B5. Each of these needs a reading of something the baseline cannot read."""
    if briefing.contact.reply_in_round:
        return "the round contains a seller reply, which needs judgement"
    if _found(items, IMAGE_KEY):
        return "the round contains image evidence, which needs judgement"
    if _found(items, HISTORY_KEY):
        return "the round contains shipment history, which needs judgement"
    return ""


def _oversize_beyond_tolerance(items: Sequence[EvidenceItem], tolerance_pct: int) -> bool | None:
    """B7 and B8's shared measurement. None when it cannot be computed.

    Size is compared as volume, because volume is what the charge is computed from: a box
    5% longer and 5% narrower is the same parcel to a rate card, and treating it as oversize
    would ask a seller for a photo of nothing.

    Returns None rather than guessing when either set of dimensions is unreadable, so B7 and
    B8 both decline and the case falls through to B9. A missing measurement that reads as
    "within tolerance" would uphold a charge nobody checked.
    """
    declared = _volume(items, DECLARED_KEY)
    measured = _volume(items, MEASURED_KEY)
    if declared is None or measured is None or declared <= 0:
        return None
    allowed = declared * (Decimal(100 + tolerance_pct) / Decimal(100))
    return measured > allowed


def _volume(items: Sequence[EvidenceItem], key: str) -> Decimal | None:
    """Volume in cm3 from a dims evidence item, as Decimal throughout (I9, ADR 0011)."""
    item = _found(items, key)
    if item is None:
        return None
    value = item.result.value or {}
    try:
        axes = [Decimal(str(value[axis])) for axis in ("length_cm", "width_cm", "height_cm")]
    except (KeyError, ArithmeticError, TypeError, ValueError):
        return None
    return axes[0] * axes[1] * axes[2]


def _found(items: Sequence[EvidenceItem], key: str) -> EvidenceItem | None:
    for item in items:
        if item.result.requirement_key == key and item.result.status is EvidenceStatus.FOUND:
            return item
    return None


def _refs(items: Sequence[EvidenceItem], keys: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(item.id) for key in keys if (item := _found(items, key)) is not None)


def _fact_ids(briefing: CaseBriefing) -> tuple[str, ...]:
    """B3 cites the facts the discrepancy came from, which is what a credit must quote (I9)."""
    return tuple(fact.fact_id for fact in briefing.assessment.facts)


__all__ = ["B7_ITEMS", "BaselineDecision", "baseline_proposal", "decide"]
