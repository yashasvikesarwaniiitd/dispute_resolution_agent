"""The decision judge (ADR 0064): checks one investigator explanation against its evidence.

It answers three questions and nothing else: which material claims the evidence does not
support, whether a suspicion is stated as fact, and whether the action is reasonable. It never
decides a case and its answer moves nothing.

**Pass or fail is code's, not the model's.** An explanation fails when the judge lists an
unsupported material claim or reports a suspicion stated as fact; the model is never asked
for a verdict it could get wrong in a way that disagrees with its own findings.

The explanation is the agent's own text, but it may echo an outside note (a hub comment that
claims a finance approval), so it reaches the prompt only as untrusted text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, ValidationError

from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.reasoning import ModelRequest
from dispute_agent.kernel.domain.values import UntrustedText
from dispute_agent.modules.reasoning.llm.calls import ask_checked

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.ports import ModelGateway, PromptRegistry

PROMPT = "decision_judge"


class Claim(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    claim: str
    why: str


class Judgement(BaseModel):
    """What the judge found. `verdict` is computed from the findings, never read from the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    unsupported_claims: tuple[Claim, ...]
    suspicion_as_fact: bool
    suspicion_detail: str = ""
    action_reasonable: bool
    action_note: str = ""
    model_id: str = ""
    prompt_version: str = ""

    @property
    def verdict(self) -> str:
        return "fail" if self.unsupported_claims or self.suspicion_as_fact else "pass"


@dataclass
class DecisionJudge:
    prompts: PromptRegistry
    gateway: ModelGateway
    version: str = "v1"
    max_output_tokens: int = 1500  # 800 cut 12 of 28 answers short (first calibration)

    def judge(
        self,
        *,
        evidence: Sequence[str],
        action: str,
        explanation: str,
        ref: str,
        credit: str = "",
    ) -> Judgement:
        """Judge one explanation. `ref` names it in the call log (a decision point or case)."""
        variables: dict[str, Any] = {
            "ctx": {"evidence": list(evidence), "action": action, "credit": credit}
        }
        rendered = self.prompts.load(
            PROMPT,
            self.version,
            variables,
            {"ctx.explanation": UntrustedText.create(explanation, "agent_rationale", ref)},
        )
        response = ask_checked(
            self.gateway,
            ModelRequest(
                purpose="decision_judge",
                prompt=rendered,
                max_output_tokens=self.max_output_tokens,
            ),
            check_judgement,
        )
        return Judgement.model_validate(
            {**dict(response.output), "model_id": response.model_id, "prompt_version": self.version}
        )


def check_judgement(output: Mapping[str, JsonValue]) -> None:
    """Refuse an answer that is not a judgement, so the one retry can correct it."""
    try:
        Judgement.model_validate(dict(output))
    except ValidationError as bad:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in bad.errors()})
        raise InvalidModelOutput(f"not a valid judgement at {fields}") from None


__all__ = ["PROMPT", "Claim", "DecisionJudge", "Judgement", "check_judgement"]
