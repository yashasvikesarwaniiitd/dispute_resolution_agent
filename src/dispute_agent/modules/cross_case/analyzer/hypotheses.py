"""The pattern analyst: competing hypotheses about a cluster code found (P7-04, I7, I14).

Called through the `ModelGateway` port like every other prompt, so the spending guard, the call
log and the failover all apply (I14). It describes; it never acts. Its output is stored as
data on the pattern, and its summary reaches the case investigator only inside
the untrusted pattern-context block (agent-design.md section 1), where it may raise caution and
never lower it.

The prompt asks for good behaviour; this module checks it, in code, before anything is
stored. An answer is refused (and retried once, then dropped) when:

- the shape is wrong (prompt schema: 2 to 4 hypotheses, the kind vocabulary, strength);
- a hypothesis has no independent confirming evidence (PA-E2);
- a hypothesis statement names a seller, hub, scanner or SKU identifier (PA-E1: identifiers
  belong in scope and evidence refs, never as the subject of a fault);
- any text accuses or proposes money: fraud, theft, blame, credit, refund, penalty,
  compensation (the manipulation example: an injected note demanding a fraud case and
  credits must produce neither);
- the evidence refs name a case the cluster does not contain, or a number appears that the
  statistics did not give (the prompt's numbers rule).

A dropped answer costs nothing but description: the pattern keeps its code-written summary,
and every caution floor applies exactly as before. Code found the cluster; code decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.errors import (
    BudgetExceeded,
    InvalidModelOutput,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.kernel.domain.reasoning import ModelRequest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.ports import ModelGateway, PromptRegistry

PROMPT = "pattern_analyst"
ATTEMPTS = 2

KINDS = {"operational", "product_packaging", "seller_process", "benign", "other"}
STRENGTHS = {"low", "medium", "high"}

#: Identifiers of the synthetic world: sellers, hubs, scanners, SKUs, cases.
IDENTIFIER = re.compile(
    r"\b(?:SELL-\d+|HUB-\d+|SCN-\d+-\d+|SKU-[A-Z0-9-]+|case_[0-9A-Za-z]+|CASE-\d+)\b"
)
ACCUSATION = re.compile(
    r"fraud|steal|stole|theft|thie[fv]|guilt|blame|culprit|credit|refund|penalt|compensat|"
    r"reimburs|punish|fine\b",
    re.IGNORECASE,
)
NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-])")


@dataclass(frozen=True)
class ClusterStats:
    """What code counted, and all the model may use."""

    dimension: str
    value: str
    cases: int
    window_days: int
    min_related_cases: int
    case_ids: tuple[str, ...]
    #: Members that could have been shown the pattern. Left out of `cases` (PA-E3).
    pattern_assisted_excluded: int = 0

    def as_json(self) -> dict[str, JsonValue]:
        return {
            "dimension": self.dimension,
            "id": self.value,
            "cases": self.cases,
            "window_days": self.window_days,
            "min_related_cases": self.min_related_cases,
            "case_ids": list(self.case_ids),
            "pattern_assisted_cases_excluded": self.pattern_assisted_excluded,
        }

    def numbers(self) -> set[str]:
        return {
            str(n)
            for n in (
                self.cases,
                self.window_days,
                self.min_related_cases,
                len(self.case_ids),
                self.pattern_assisted_excluded,
            )
        }


@dataclass(frozen=True)
class Hypotheses:
    summary: str
    hypotheses: tuple[dict[str, JsonValue], ...]
    investigation_request: dict[str, JsonValue]
    evidence_strength: str
    model_id: str
    prompt_hash: str


@dataclass
class PatternAnalyst:
    prompts: PromptRegistry
    gateway: ModelGateway
    version: str = "v1"
    max_output_tokens: int = 4000  # reasoning tokens count too (foundry.md section 6)
    refusals: list[str] = field(default_factory=list)

    def describe(
        self,
        stats: ClusterStats,
        *,
        prior_findings: Sequence[Mapping[str, str]] = (),
        case_notes: Sequence[Mapping[str, str]] = (),
    ) -> Hypotheses | None:
        """Hypotheses, or None when the model is unavailable or its answers fail the checks."""
        variables: dict[str, Any] = {
            "cluster": stats.as_json(),
            "prior_findings": [dict(f) for f in prior_findings],
            # Neutralised by the template inside the untrusted case-notes block (I5).
            "case_notes": [dict(n) for n in case_notes],
        }
        try:
            rendered = self.prompts.load(PROMPT, self.version, variables, {})
        except Exception:  # an inactive or missing prompt: no description, never a guess
            return None
        request = ModelRequest(
            case_id=None,
            purpose="pattern_analyst",
            prompt=rendered,
            max_output_tokens=self.max_output_tokens,
        )
        for _ in range(ATTEMPTS):
            try:
                response = self.gateway.complete(request)
            except InvalidModelOutput as refused:
                # The gateway's own schema check (after its own retry), e.g. a cut-off answer.
                self.refusals.append(f"gateway: {str(refused.args[0])[:100]}")
                continue
            except (TransientPortError, ModuleUnavailable, BudgetExceeded):
                return None
            try:
                return _checked(response.output, stats, response.model_id, rendered.hash)
            except InvalidModelOutput as refused:
                # The check's own words, never the model's text (I12): which rule refused it.
                self.refusals.append(str(refused.args[0])[:120])
                continue
        return None


def _checked(
    output: Mapping[str, JsonValue], stats: ClusterStats, model_id: str, prompt_hash: str
) -> Hypotheses:
    summary = _text(output.get("summary"), 600, "summary")
    hypotheses = output.get("hypotheses")
    if not isinstance(hypotheses, list) or not 2 <= len(hypotheses) <= 4:
        raise InvalidModelOutput("hypotheses: 2 to 4 required")
    kept: list[dict[str, JsonValue]] = []
    for item in hypotheses:
        if not isinstance(item, dict):
            raise InvalidModelOutput("hypothesis is not an object")
        statement = _text(item.get("statement"), 400, "statement")
        if IDENTIFIER.search(statement):
            raise InvalidModelOutput("PA-E1: an identifier is the subject of a hypothesis")
        if item.get("kind") not in KINDS:
            raise InvalidModelOutput("hypothesis kind outside the vocabulary")
        confirm = _text(item.get("confirm_with"), 400, "confirm_with")
        refute = _text(item.get("refute_with"), 400, "refute_with")
        owner = _text(item.get("owner_team"), 80, "owner_team")
        kept.append(
            {
                "statement": statement,
                "kind": str(item["kind"]),
                "confirm_with": confirm,
                "refute_with": refute,
                "owner_team": owner,
            }
        )
    request = output.get("investigation_request")
    if not isinstance(request, dict):
        raise InvalidModelOutput("investigation_request missing")
    refs = request.get("evidence_refs")
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise InvalidModelOutput("evidence_refs must be a list of ids")
    allowed_refs = {*stats.case_ids, stats.value}
    if any(r not in allowed_refs for r in refs):
        raise InvalidModelOutput("evidence_refs name something the cluster does not contain")
    investigation: dict[str, JsonValue] = {
        "title": _text(request.get("title"), 150, "title"),
        "scope": _text(request.get("scope"), 400, "scope"),
        "question": _text(request.get("question"), 400, "question"),
        "evidence_refs": list(refs),
    }
    strength = output.get("evidence_strength")
    if strength not in STRENGTHS:
        raise InvalidModelOutput("evidence_strength outside low, medium, high")

    prose = [
        summary,
        *(str(h[k]) for h in kept for k in ("statement", "confirm_with", "refute_with")),
    ]
    prose += [str(investigation["title"]), str(investigation["question"])]
    for text in prose:
        if ACCUSATION.search(text):
            raise InvalidModelOutput("accusation or money in a hypothesis: never")
        for number in NUMBER.findall(IDENTIFIER.sub(" ", text)):
            if number not in stats.numbers():
                raise InvalidModelOutput("a number the statistics did not give")
    return Hypotheses(
        summary=summary,
        hypotheses=tuple(kept),
        investigation_request=investigation,
        evidence_strength=str(strength),
        model_id=model_id,
        prompt_hash=prompt_hash,
    )


def _text(value: object, limit: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidModelOutput(f"{name}: non-empty text required")
    if len(value) > limit:
        raise InvalidModelOutput(f"{name}: longer than {limit}")
    return value.strip()
