"""The briefing as the case_investigator template sees it (agent-design.md section 1).

`CaseBriefing` is the kernel's record of what the reasoner may see. This turns it into the
`b` the template renders, applying the per-field caps from the spec's table, and splits the
outside text off into its own map so it can only reach the prompt through the registry's
`untrusted` argument (I5).

The caps are the point of this file. A model's attention is finite and a seller's email is
not: without a cap one long reply would crowd the assessment out of the prompt, and the
case would be decided on the seller's framing rather than on the numbers. So outside text is
trimmed head-and-tail with an explicit marker - the model is told that it did not see all
of it - and trusted values that are too big are replaced by their keys rather than cut
mid-JSON.

Prototype scope (ADR 0029): the total-length shrink order in agent-design.md section 1 is
not implemented; with every per-field cap applied the briefing cannot exceed the total, so
the shrink order only matters once the caps change (BACKLOG.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import Provenance
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.kernel.policy.numbers import render_fact

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.evidence import ChargeAssessment, EvidenceItem
    from dispute_agent.kernel.domain.reasoning import CaseBriefing

MAX_TRUSTED_ITEMS = 12
MAX_TRUSTED_VALUE_CHARS = 700
MAX_UNTRUSTED_ITEMS = 5
UNTRUSTED_HEAD, UNTRUSTED_TAIL = 750, 250
MAX_FACTS = 40
MAX_FACT_CHARS = 100
MAX_RELATED = 10
MAX_PATTERNS = 5
MAX_REJECTIONS, MAX_REJECTION_CHARS = 3, 400
MAX_SCREEN_RESULTS = 10

#: The dispute statement's id in the briefing. Always kept when texts are dropped (spec).
STATEMENT_ID = "dispute_statement"


@dataclass(frozen=True)
class BriefingView:
    """`b` for the template, the outside texts keyed by id, and every id the model may cite."""

    b: dict[str, Any]
    untrusted: dict[str, UntrustedText]
    citable_ids: frozenset[str]


def build_view(briefing: CaseBriefing) -> BriefingView:
    trusted: list[dict[str, Any]] = []
    outside: list[tuple[str, str, UntrustedText]] = []

    if briefing.facts.statement is not None:
        outside.append((STATEMENT_ID, "dispute_form", briefing.facts.statement))

    for item in briefing.evidence.items:
        result = item.result
        if item.provenance is Provenance.SELLER and result.value is not None:
            # A seller-provided structured value is still the seller's words (spec, row
            # "SELLER-provenance evidence item value"): it renders as outside text.
            as_text = json.dumps(result.value, sort_keys=True, default=str)
            outside.append((str(item.id), result.source, _untrusted(as_text, str(item.id))))
        elif item.provenance in (Provenance.SYSTEM, Provenance.OPS):
            trusted.append(_trusted_item(item))
        for n, text in enumerate(result.untrusted):
            outside.append((f"{item.id}.{n}", result.source, text))

    kept = _keep_outside(outside)
    untrusted = {doc_id: _trim(text) for doc_id, _, text in kept}
    assessment = _assessment_view(briefing.assessment)

    b: dict[str, Any] = {
        "case_ref": str(briefing.case_id),
        "facts": {
            "shipment_ref": briefing.facts.shipment_ref,
            "claim_ref": briefing.facts.claim_ref,
            "seller_ref": briefing.facts.seller_ref,
            "raised_at": briefing.facts.raised_at.isoformat(),
        },
        "assessment_view": assessment,
        "trusted_evidence": trusted[:MAX_TRUSTED_ITEMS],
        "untrusted_evidence": [{"id": doc_id, "source": source} for doc_id, source, _ in kept],
        # The reply interpreter arrives in Phase 4+; until then there is no reading.
        "reply_reading": None,
        "contact": briefing.contact.model_dump(mode="json"),
        "screen_results": [
            {
                "evidence_item_id": str(sr.evidence_item_id or ""),
                "kind": sr.kind.value,
                "flags": list(sr.flags),
            }
            for sr in briefing.screen_results[:MAX_SCREEN_RESULTS]
        ],
        "related_cases": [
            # Outcome only, and no amount: nothing from another case's money reaches the
            # model (spec section 3, T12).
            {
                "case_ref": str(rc.case_id),
                "state": rc.state.value,
                "closed_at": rc.closed_at.isoformat() if rc.closed_at else None,
            }
            for rc in briefing.related_cases[:MAX_RELATED]
        ],
        "pattern_context": [
            {"id": str(p.id), "status": str(p.status), "summary": p.summary}
            for p in briefing.pattern_context[:MAX_PATTERNS]
        ],
        "independent_view": (
            briefing.independent_view.model_dump(mode="json") if briefing.independent_view else None
        ),
        "prior_rounds": [r.model_dump(mode="json") for r in briefing.prior_rounds],
        "prior_rejections": [
            note[:MAX_REJECTION_CHARS] for note in briefing.prior_rejections[:MAX_REJECTIONS]
        ],
        "allowed_actions": [a.value for a in briefing.allowed_actions],
    }

    citable = (
        {f["fact_id"] for f in assessment["facts"]}
        | {t["id"] for t in b["trusted_evidence"]}
        | set(untrusted)
        | {str(item.id) for item in briefing.evidence.items}
    )
    return BriefingView(b=b, untrusted=untrusted, citable_ids=frozenset(citable))


#: The historical comparison (ADR 0060). Computed in code from the seller's own trusted past
#: shipments, so it is trusted; too long for the generic limit, so it gets a compact view.
COMPARABLE_KEY = "shipment.comparable_history"
COMPARISON_FIELDS = (
    "verdict",
    "references",
    "current",
    "typical",
    "range",
    "flags",
    "excluded",
    "differs_by",
)


def _comparison_view(value: dict[str, Any]) -> dict[str, Any]:
    """What the model needs to read the comparison, within MAX_TRUSTED_VALUE_CHARS.

    Every figure is kept; only the list of reference ids is shortened, from the end, if the
    view would not fit ("+N more" says how many were left out). Nothing else from the stored
    value is added: no outcome, no other seller, no hub finding - the value never holds them.
    """
    view = {k: value[k] for k in COMPARISON_FIELDS if value.get(k) not in (None, {}, [])}
    refs = [str(r) for r in value.get("reference_refs") or []]
    for keep in range(len(refs), -1, -1):
        shown = refs[:keep] + ([f"+{len(refs) - keep} more"] if keep < len(refs) else [])
        candidate = {**view, "reference_refs": shown} if refs else view
        if len(json.dumps(candidate, sort_keys=True)) <= MAX_TRUSTED_VALUE_CHARS:
            return candidate
    return view


#: What each evidence item is, in words (ADR 0072). Before this the model saw only an id, a
#: source and a value, and had to guess which item was which from the value's shape.
LABELS = {
    "shipment.status": "shipment status",
    "shipment.declared_dims": "declared size and weight (from the booking)",
    "shipment.measured_dims": "hub measurement of this parcel",
    "shipment.zone": "zone and booked packaging",
    "ledger.entries": "billing ledger entries for this claim",
    "shipment.image_description": "photo flag",
    "shipment.history": "this parcel's scan and tracking history",
    "ops.finding": "the hub's answer to a remeasurement request",
    COMPARABLE_KEY: "authoritative comparison with the seller's past shipments (code-computed)",
    "parcel.image_check": "image check of the parcel photo",
}


def _trusted_item(item: EvidenceItem) -> dict[str, Any]:
    value: Any = item.result.value
    if item.result.requirement_key == COMPARABLE_KEY and isinstance(value, dict):
        value = _comparison_view(value)
    if value is not None:
        rendered = json.dumps(value, sort_keys=True, default=str)
        if len(rendered) > MAX_TRUSTED_VALUE_CHARS:
            value = {"truncated": True, "keys": sorted(value)}
        else:
            value = json.loads(rendered)
    key = item.result.requirement_key
    return {
        "id": str(item.id),
        "key": key,
        "label": LABELS.get(key, key.replace("_", " ").replace(".", ": ")),
        "source": item.result.source,
        "status": item.result.status.value,
        "value": value,
    }


def _assessment_view(assessment: ChargeAssessment) -> dict[str, Any]:
    def money(value: object) -> str | None:
        return None if value is None else render_fact(value)

    return {
        "eligible": assessment.eligible,
        "reason_codes": list(assessment.reason_codes),
        "charged": money(assessment.charged),
        "recalculated": money(assessment.recalculated),
        "recalculated_remeasured": money(assessment.recalculated_remeasured),
        "discrepancy": money(assessment.discrepancy),
        "definite_errors": list(assessment.definite_errors),
        "facts": [
            {
                "fact_id": fact.fact_id,
                "label": fact.label,
                "value": render_fact(fact.value)[:MAX_FACT_CHARS],
            }
            for fact in assessment.facts[:MAX_FACTS]
        ],
    }


def _keep_outside(
    outside: list[tuple[str, str, UntrustedText]],
) -> list[tuple[str, str, UntrustedText]]:
    """At most five texts: the dispute statement always, then the newest of the rest."""
    statement = [entry for entry in outside if entry[0] == STATEMENT_ID]
    rest = [entry for entry in outside if entry[0] != STATEMENT_ID]
    room = MAX_UNTRUSTED_ITEMS - len(statement)
    return statement + (rest[-room:] if room > 0 else [])


def _trim(text: UntrustedText) -> UntrustedText:
    """Head and tail with an explicit marker, so the model knows it did not see it all."""
    body = text.text
    limit = UNTRUSTED_HEAD + UNTRUSTED_TAIL
    if len(body) <= limit:
        return text
    omitted = len(body) - limit
    trimmed = (
        f"{body[:UNTRUSTED_HEAD]}\n[... {omitted} characters omitted ...]\n{body[-UNTRUSTED_TAIL:]}"
    )
    return UntrustedText.create(trimmed, text.origin, text.source_ref)


def _untrusted(text: str, source_ref: str) -> UntrustedText:
    return UntrustedText.create(text, "seller_portal", source_ref)


__all__ = ["STATEMENT_ID", "BriefingView", "build_view"]
