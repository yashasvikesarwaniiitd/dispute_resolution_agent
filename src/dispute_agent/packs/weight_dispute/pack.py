"""The weight-dispute pack: domain knowledge, and nothing else (P2-01, CLAUDE.md section 4).

This class is everything the system knows about shipping weight disputes. It never reaches a
database, a channel or a model — `make arch` enforces that `packs/` imports only `kernel`,
which is what lets a second pack be added later without the kernel learning anything about
parcels.

`assess` is the piece that matters most. It turns a round of evidence into the numbers a
seller may be shown, and it has two properties worth stating out loud:

**It is deterministic (I9).** The same bundle gives the same assessment, byte for byte,
and `input_hash` is the proof: it is the hash of the bundle the assessment was computed
from, so a stored assessment can be shown to belong to the evidence it claims rather than to
whatever the evidence looks like today.

**It never raises for missing evidence — it reports it.** An assessment that threw on an
incomplete bundle would turn "we do not know yet" into a crash, and the round that failed to
fetch a measurement is exactly the round where a human needs to see what happened. Every
number it could not compute comes back as `None`, and the baseline's B1 reads that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import CaseState, EvidenceStatus, ProposalAction
from dispute_agent.kernel.domain.errors import DomainValidationError
from dispute_agent.kernel.domain.evidence import ChargeAssessment, EvidenceRequirement, Fact
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import AssessmentId
from dispute_agent.kernel.domain.values import Money
from dispute_agent.packs.weight_dispute import eligibility as eligibility_rules
from dispute_agent.packs.weight_dispute.baseline import baseline_proposal, decide
from dispute_agent.packs.weight_dispute.calculator import Dimensions, calculate
from dispute_agent.packs.weight_dispute.rate_card import DEFAULT_RATE_CARD
from dispute_agent.packs.weight_dispute.reconcile import LedgerLine, prove

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import (
        EligibilityResult,
        EvidenceBundle,
        EvidenceItem,
    )
    from dispute_agent.kernel.domain.ids import CaseId, ProposalId
    from dispute_agent.kernel.domain.json_values import JsonScalar, JsonValue
    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.packs.weight_dispute.rate_card import RateCard

NAME = "weight_dispute"
VERSION = "1.0.0"

STATUS = "shipment.status"
DECLARED = "shipment.declared_dims"
MEASURED = "shipment.measured_dims"
ZONE = "shipment.zone"
LEDGER = "ledger.entries"
IMAGE = "shipment.image_description"
HISTORY = "shipment.history"
FINDING = "ops.finding"

#: What a weight dispute needs to know, and which of it the assessment cannot do without.
#: `required` is the whole of B1: an image is nice to have, a measurement is the case.
EVIDENCE_PLAN: tuple[EvidenceRequirement, ...] = (
    EvidenceRequirement(key=STATUS, source="evidence", required=True),
    EvidenceRequirement(key=DECLARED, source="evidence", required=True),
    EvidenceRequirement(key=MEASURED, source="evidence", required=True),
    EvidenceRequirement(key=ZONE, source="evidence", required=True),
    EvidenceRequirement(key=LEDGER, source="evidence", required=True),
    EvidenceRequirement(key=IMAGE, source="evidence", required=False),
    EvidenceRequirement(key=HISTORY, source="evidence", required=False),
    EvidenceRequirement(key=FINDING, source="evidence", required=False),
)

REQUIRED_KEYS = tuple(r.key for r in EVIDENCE_PLAN if r.required)

#: The pack's full menu. The kernel then subtracts `propose_credit` and `uphold_charge` when
#: the case has a live or verified credit — the pack cannot know about credits on other
#: cases, so it must not try to (orchestrator.md section 3.6).
FULL_MENU: tuple[ProposalAction, ...] = (
    ProposalAction.PROPOSE_CREDIT,
    ProposalAction.UPHOLD_CHARGE,
    ProposalAction.REQUEST_SELLER_EVIDENCE,
    ProposalAction.REQUEST_REMEASUREMENT,
    ProposalAction.WAIT,
    ProposalAction.ESCALATE_TO_HUMAN,
)


class WeightDisputePack:
    """Implements DomainPack."""

    name = NAME
    version = VERSION

    def __init__(self, card: RateCard | None = None, *, tolerance_pct: int = 5) -> None:
        self._card = card or DEFAULT_RATE_CARD
        self._tolerance_pct = tolerance_pct

    # -- intake ---------------------------------------------------------------------------

    def parse_facts(self, raw: Mapping[str, JsonValue]) -> Mapping[str, JsonScalar]:
        """Flat scalars only, so the kernel stays domain-neutral and the pack owns meaning.

        A nested value is refused rather than flattened: the kernel stores `pack_facts` as
        scalars, and quietly stringifying a dictionary here would put a value into the
        database that nothing can read back.
        """
        out: dict[str, JsonScalar] = {}
        for key, value in raw.items():
            if isinstance(value, float):
                raise DomainValidationError(
                    f"pack_facts[{key!r}] is a float; weight and money are integers and "
                    "dimensions are Decimal (ADR 0011)"
                )
            if value is not None and not isinstance(value, str | int | bool):
                raise DomainValidationError(
                    f"pack_facts[{key!r}] must be a flat scalar, got {type(value).__name__}"
                )
            out[key] = value
        return out

    def eligibility(self, facts: CaseFacts) -> EligibilityResult:
        return eligibility_rules.eligibility(facts)

    def evidence_plan(self, facts: CaseFacts) -> Sequence[EvidenceRequirement]:
        return EVIDENCE_PLAN

    # -- the numbers ----------------------------------------------------------------------

    def assess(
        self,
        case_id: CaseId,
        gather_round: int,
        bundle: EvidenceBundle,
        *,
        claim_ref: str | None = None,
    ) -> ChargeAssessment:
        """Recalculate the charge and reconcile it against the ledger. Never raises.

        `claim_ref` says which charge line the dispute is about. Without it this had to
        take the first DEBIT in the round and hope, and a shipment with two charge lines
        would recalculate one dispute against the other's charge - proving an error worth
        the difference between two unrelated bills (I9). The `DomainPack` port does not
        carry it yet; see BACKLOG.
        """
        charged = _charged(bundle, claim_ref)
        measured = _dims(bundle, MEASURED)
        dead = _int(bundle, MEASURED, "dead_grams")
        zone = _str(bundle, ZONE, "zone")
        packaging = _str(bundle, ZONE, "packaging")

        recalculated: Money | None = None
        remeasured_charge: Money | None = None
        duplicate_total: Money | None = None
        definite_errors: tuple[str, ...] = ()
        facts: list[Fact] = []

        if measured is not None and dead is not None and zone is not None:
            charge = calculate(measured, dead, zone, self._card, packaging)
            recalculated = charge.amount
            facts.extend(_charge_facts(charge, bundle))

            if charged is not None and claim_ref is not None:
                remeasured, remeasured_dead = _finding_dims(bundle)
                reconciliation = prove(
                    charged=charged,
                    billed_dimensions=measured,
                    billed_dead_grams=dead,
                    zone=zone,
                    card=self._card,
                    ledger=_ledger_lines(bundle),
                    claim_ref=claim_ref,
                    packaging=packaging,
                    remeasured=remeasured,
                    remeasured_dead_grams=remeasured_dead,
                )
                definite_errors = reconciliation.definite_errors
                remeasured_charge = reconciliation.recalculated_remeasured
                duplicate_total = reconciliation.duplicate_debit_total

        # The absolute difference, which is what `ChargeAssessment` validates against.
        # `difference` clamps an undercharge to zero because no credit follows from one;
        # using it here made `assess` raise on a bill that was too low, which contradicts
        # the one thing this method promises - that it reports rather than raises.
        discrepancy = (
            _gap(charged, recalculated)
            if charged is not None and recalculated is not None
            else None
        )
        if charged is not None:
            facts.append(Fact(fact_id="f_charged", label="amount charged", value=charged))
        if discrepancy is not None:
            facts.append(Fact(fact_id="f_discrepancy", label="overcharge", value=discrepancy))

        eligible, reason_codes = _eligible_from_evidence(bundle)
        return ChargeAssessment(
            id=AssessmentId(f"asm_{case_id}_{gather_round}"),
            case_id=case_id,
            gather_round=gather_round,
            pack_version=VERSION,
            eligible=eligible,
            reason_codes=reason_codes,
            charged=charged,
            recalculated=recalculated,
            discrepancy=discrepancy,
            recalculated_remeasured=remeasured_charge,
            duplicate_debit_total=duplicate_total,
            definite_errors=definite_errors,
            facts=tuple(facts),
            input_hash=payload_hash(bundle.model_dump(mode="json")),
        )

    # -- the menu and the fallback ---------------------------------------------------------

    def allowed_actions(
        self, state: CaseState, assessment: ChargeAssessment
    ) -> Sequence[ProposalAction]:
        """Always includes `escalate_to_human`, so there is never an empty menu.

        A pack that could return nothing would leave the reasoner with no legal answer, and
        the one answer that is always legal is "a person should look at this".
        """
        if assessment.eligible != "eligible":
            # Not just "ineligible". An `unknown` eligibility with the full menu lets B3
            # credit a parcel nobody could show was delivered, and money-rules.md section 1
            # requires eligibility to be `eligible` before any credit. The eligibility step
            # already sends `unknown` to a person; this is the same answer one step later,
            # for the case where the evidence round is what made it unknown.
            return (ProposalAction.ESCALATE_TO_HUMAN,)
        if state is not CaseState.INVESTIGATING:
            return (ProposalAction.ESCALATE_TO_HUMAN,)
        return FULL_MENU

    def baseline_proposal(
        self,
        briefing: CaseBriefing,
        *,
        proposal_id: ProposalId,
        now: datetime,
        fallback_used: bool = False,
    ) -> Proposal:
        """Rows B1-B9 (agent-design.md section 5).

        The port's signature takes the briefing alone; the id and the clock are passed in
        here because this pack holds neither — it is domain knowledge, not a composition
        root, and a pack that generated its own ids would not be deterministic.
        """
        return baseline_proposal(
            briefing,
            proposal_id=proposal_id,
            now=now,
            required_keys=REQUIRED_KEYS,
            tolerance_pct=self._tolerance_pct,
            fallback_used=fallback_used,
        )

    def baseline_row(self, briefing: CaseBriefing) -> str:
        """Which row fired, for tests and for explaining a decision. No side effects."""
        return decide(briefing, required_keys=REQUIRED_KEYS, tolerance_pct=self._tolerance_pct).row

    def policy_defaults(self) -> Mapping[str, JsonValue]:
        """What the pack asks policy for. The config may override any of it."""
        return {"baseline": {"tolerance_pct": self._tolerance_pct}}


# -- reading one round of evidence ----------------------------------------------------------
#
# Every reader below answers None rather than raising. A bundle is whatever the gather round
# managed to collect, and `assess` reporting "I could not compute this" is the behaviour I4
# wants; an exception here would lose the whole round including the parts that worked.


def _found(bundle: EvidenceBundle, key: str) -> EvidenceItem | None:
    for item in bundle.items:
        if item.result.requirement_key == key and item.result.status is EvidenceStatus.FOUND:
            return item
    return None


def _value(bundle: EvidenceBundle, key: str) -> Mapping[str, object]:
    item = _found(bundle, key)
    return item.result.value or {} if item is not None else {}


def _str(bundle: EvidenceBundle, key: str, field: str) -> str | None:
    value = _value(bundle, key).get(field)
    return value if isinstance(value, str) else None


def _int(bundle: EvidenceBundle, key: str, field: str) -> int | None:
    value = _value(bundle, key).get(field)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _dims(bundle: EvidenceBundle, key: str) -> Dimensions | None:
    value = _value(bundle, key)
    try:
        return Dimensions.of(value["length_cm"], value["width_cm"], value["height_cm"])
    except (KeyError, DomainValidationError, ArithmeticError, TypeError, ValueError):
        return None


def _ledger_lines(bundle: EvidenceBundle) -> tuple[LedgerLine, ...]:
    entries = _value(bundle, LEDGER).get("entries")
    if not isinstance(entries, list):
        return ()
    lines = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        try:
            lines.append(
                LedgerLine(
                    transaction_id=str(entry["transaction_id"]),
                    kind=str(entry["kind"]),
                    amount_paise=int(entry["amount_paise"]),
                    claim_ref=str(entry["claim_ref"]),
                    reason_code=entry.get("reason_code"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(lines)


def _gap(charged: Money, recalculated: Money) -> Money:
    """|charged - recalculated|, the quantity `ChargeAssessment` validates."""
    return charged - recalculated if charged >= recalculated else recalculated - charged


def _charged(bundle: EvidenceBundle, claim_ref: str | None) -> Money | None:
    """What the seller was billed on *this* charge line: its first DEBIT.

    From the ledger rather than from the dispute statement, always. The seller's own figure
    is outside text (I5) and the amount a credit is computed against must be the one the
    books hold.

    With no claim to scope it to, this answers None rather than taking whichever row came
    first. "We do not know what was charged" is a true statement the assessment can carry;
    a number belonging to another charge line is not.
    """
    if claim_ref is None:
        return None
    debits = [
        line
        for line in _ledger_lines(bundle)
        if line.kind == "DEBIT" and line.claim_ref == claim_ref
    ]
    return Money.from_paise(debits[0].amount_paise) if debits else None


def _finding_dims(bundle: EvidenceBundle) -> tuple[Dimensions | None, int | None]:
    """A hub remeasurement, if this round carries one with actual numbers on it.

    An `inspected` or `no_fault_found` finding carries no measurements, and a note on it is
    free text that can never prove a credit (money-rules.md section 2).
    """
    item = _found(bundle, FINDING)
    if item is None or (item.result.value or {}).get("kind") != "remeasured":
        return None, None
    return _dims(bundle, FINDING), _int(bundle, FINDING, "dead_grams")


def _charge_facts(charge: object, bundle: EvidenceBundle) -> list[Fact]:
    """The numbers a message may quote, cited by id rather than by value (I9)."""
    refs = tuple(item.id for key in (MEASURED, ZONE) if (item := _found(bundle, key)) is not None)
    return [
        Fact(
            fact_id="f_chargeable_grams",
            label="chargeable weight in grams",
            value=charge.chargeable_grams,  # type: ignore[attr-defined]
            source_items=refs,
        ),
        Fact(
            fact_id="f_recalculated",
            label="recalculated charge",
            value=charge.amount,  # type: ignore[attr-defined]
            source_items=refs,
        ),
    ]


def _eligible_from_evidence(
    bundle: EvidenceBundle,
) -> tuple[str, tuple[str, ...]]:
    """S32: the evidence round can prove a shipment ineligible that intake could not.

    Intake may not have known the status; the scan history fetched here does. An unreadable
    status stays `unknown` rather than becoming eligible — proceeding on a parcel nobody can
    show was delivered is the guess this refuses to make.
    """
    status = _latest_scan(bundle) or _str(bundle, STATUS, "status")
    if status is None:
        return "unknown", (eligibility_rules.STATUS_UNKNOWN_CODE,)
    if status == eligibility_rules.DELIVERED:
        return "eligible", ()
    if status.upper() in eligibility_rules.NOT_DELIVERED:
        return "ineligible", (eligibility_rules.NOT_DELIVERED_CODE,)
    return "unknown", (eligibility_rules.STATUS_UNKNOWN_CODE,)


def _latest_scan(bundle: EvidenceBundle) -> str | None:
    """The status on the last scan in the history, if the history says one.

    This is what makes S32 work: a parcel marked DELIVERED at intake and scanned RTO two
    days later is not a delivered parcel, and the gather round is the first time the system
    sees that. The last scan wins because scans are a sequence of events, not opinions.

    A scan whose trailing word is not a status this pack knows is ignored rather than
    guessed at. An unparsed line must not quietly become "we could not tell", which would
    turn a formatting change in the scan feed into a wave of escalated cases.
    """
    entries = _value(bundle, HISTORY).get("entries")
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if not isinstance(entry, str) or not entry.split():
            continue
        last = entry.split()[-1].upper()
        if last == eligibility_rules.DELIVERED or last in eligibility_rules.NOT_DELIVERED:
            return last
    return None


__all__ = ["EVIDENCE_PLAN", "FULL_MENU", "REQUIRED_KEYS", "WeightDisputePack"]
