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


def _trusted_item(item: EvidenceItem) -> dict[str, Any]:
    value: Any = item.result.value
    if value is not None:
        rendered = json.dumps(value, sort_keys=True, default=str)
        if len(rendered) > MAX_TRUSTED_VALUE_CHARS:
            value = {"truncated": True, "keys": sorted(value)}
        else:
            value = json.loads(rendered)
    return {
        "id": str(item.id),
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
