"""The model-backed reasoner (P4-05, agent-design.md sections 1-4).

It renders the case_investigator prompt for one briefing, asks the model through the
gateway, and turns the answer into a `Proposal`. That is all it does: it holds no store, no
channel and no executor, so "the AI never writes" (I1) is still a fact about its type.

**What it checks, beyond the schema the gateway already enforced** (spec section 3). The
schema can say "an array of strings"; it cannot say "strings that are ids in this briefing".
So after the gateway hands back a well-formed answer, this checks what only the briefing
knows: the action is on the menu, every cited id exists (threat T12 - a fabricated citation
is how a model makes an unsupported claim look supported), each action carries the
parameters it needs, and no pattern is claimed to have been considered when none was shown.
A failure gets one identical retry, then `InvalidModelOutput` - and the case goes to a
person, never to the rules baseline (FLOW section 7b: a model that answered badly is not a
model that was unavailable).

**What it does not do** is fall back. Falling back to rules when the model is unreachable is
the investigate step's job (orchestrator.md section 5), because the step is the one place
that also has to record that it happened.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from dispute_agent.kernel.domain.enums import ProposalAction
from dispute_agent.kernel.domain.errors import InvalidModelOutput
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ProposalId
from dispute_agent.kernel.domain.reasoning import ModelRequest, Proposal
from dispute_agent.modules.reasoning.llm.briefing_view import BriefingView, build_view

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dispute_agent.kernel.domain.json_values import JsonValue
    from dispute_agent.kernel.domain.reasoning import CaseBriefing, ModelResponse
    from dispute_agent.kernel.ports import Clock, IdGenerator, ModelGateway, PromptRegistry

ADAPTER = "llm"
PROMPT = "case_investigator"

#: Spec section 4: one identical retry when a section-3 check fails.
ATTEMPTS = 2


@dataclass
class LlmReasoner:
    """Implements Reasoner."""

    prompts: PromptRegistry
    gateway: ModelGateway
    ids: IdGenerator
    clock: Clock
    version: str = "v1"
    max_output_tokens: int = 1200

    def propose(self, briefing: CaseBriefing) -> Proposal:
        view = build_view(briefing)
        rendered = self.prompts.load(PROMPT, self.version, {"b": view.b}, view.untrusted)
        request = ModelRequest(
            case_id=briefing.case_id,
            purpose="case_investigator",
            prompt=rendered,
            max_output_tokens=self.max_output_tokens,
        )
        # Spec section 1: the briefing hash is over the rendered message, so a proposal can
        # be reproduced exactly from what the model was actually shown.
        briefing_hash = payload_hash(rendered.messages[-1].content)

        last: InvalidModelOutput | None = None
        for _ in range(ATTEMPTS):
            response = self.gateway.complete(request)
            try:
                check(response.output, briefing, view)
                return self._proposal(response, briefing, briefing_hash, rendered.hash)
            except InvalidModelOutput as invalid:
                last = invalid
        assert last is not None
        raise InvalidModelOutput(f"proposal checks failed twice; last: {last.args[0]}")

    def _proposal(
        self,
        response: ModelResponse,
        briefing: CaseBriefing,
        briefing_hash: str,
        prompt_hash: str,
    ) -> Proposal:
        out: Mapping[str, Any] = response.output
        try:
            return Proposal.model_validate(
                {
                    "id": ProposalId(self.ids.new_id("prp")),
                    "case_id": briefing.case_id,
                    "gather_round": briefing.evidence.gather_round,
                    "action": out["action"],
                    "confidence": out["confidence"],
                    "rationale": out["rationale"],
                    "findings": out["findings"],
                    "action_parameters": out["action_parameters"],
                    "pattern_influence": out["pattern_influence"],
                    "manipulation_flags": out["manipulation_flags"],
                    "reasoner": "llm",
                    "prompt_name": PROMPT,
                    "prompt_version": self.version,
                    "prompt_hash": prompt_hash,
                    "model_id": response.model_id,
                    "model_route": response.route,
                    "briefing_hash": briefing_hash,
                    "created_at": self.clock.now(),
                },
                strict=False,
            )
        except ValidationError as bad:
            # Field names only: the values may echo case data into a log (I12).
            fields = sorted({".".join(str(p) for p in e["loc"]) for e in bad.errors()})
            raise InvalidModelOutput(f"not a valid proposal at {fields}") from None


def check(output: Mapping[str, JsonValue], briefing: CaseBriefing, view: BriefingView) -> None:
    """agent-design.md section 3, checks 1-4. Raises InvalidModelOutput."""
    action = str(output.get("action"))
    if action not in {a.value for a in briefing.allowed_actions}:
        raise InvalidModelOutput(f"action {action!r} is not on this case's menu")

    findings: Any = output.get("findings") or []
    for finding in findings:
        for ref in finding.get("evidence_refs", []):
            if ref not in view.citable_ids:
                # The id is model-written; it is not quoted back, only counted.
                raise InvalidModelOutput("a finding cites an id that is not in the briefing")

    params: Any = output.get("action_parameters") or {}
    if action == ProposalAction.PROPOSE_CREDIT and not params.get("reason_code"):
        raise InvalidModelOutput("propose_credit without a reason_code")
    if action == ProposalAction.REQUEST_SELLER_EVIDENCE and not (
        1 <= len(params.get("requested_items") or []) <= 3
    ):
        raise InvalidModelOutput("request_seller_evidence needs 1-3 requested_items")
    if action == ProposalAction.REQUEST_REMEASUREMENT and not params.get("remeasurement_question"):
        raise InvalidModelOutput("request_remeasurement without a remeasurement_question")

    influence: Any = output.get("pattern_influence") or {}
    if not briefing.pattern_context and influence.get("considered"):
        raise InvalidModelOutput("claims to have considered patterns, but none were shown")


__all__ = ["ADAPTER", "PROMPT", "LlmReasoner", "check"]
