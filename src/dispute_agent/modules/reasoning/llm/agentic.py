"""The agentic investigator: the AI chooses its own lookups, checks its draft, then decides (ADR 0042).

The single-call investigator gets one briefing and answers once. The briefing is cut to fit
(an evidence value over 700 characters is replaced by its field names, at most 12 evidence items
and 5 outside texts are shown), so the model often decides on partial information with no way
to ask for more. This investigator works in steps. Each turn the model returns its next move,
an argument and its current draft proposal:

- `open_evidence <id>` - the full value of one evidence item (outside-party text comes back
  neutralised, in an untrusted block);
- `policy_lookup <query>` - passages from the policy rules and decision records (ADR 0040);
- `check_draft` - the same checks its final answer will face (menu, cited ids, parameters);
- `decide` - the draft is the proposal.

Code runs each move and shows the result on the next turn. At most `MAX_TURNS` turns; on the last
one the model must decide. The decision goes through exactly the checks the single-call
investigator applies, so nothing the agent does can make an action more allowed.

**What it can and cannot do.** Every tool reads the briefing already in memory or the local
policy index: no database, no network, no write port (I1). Every turn is a normal model call
through the guarded `ModelGateway` - caps, call log, failover and screening all apply (I14). It
keeps the same per-case call budget as the single-call engine: it spends extra turns only when
it chooses to look something up, and a case whose budget runs out goes to a person.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from dispute_agent.kernel.domain.enums import Provenance
from dispute_agent.kernel.domain.errors import (
    InvalidModelOutput,
    ModuleUnavailable,
    TransientPortError,
)
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.reasoning import ModelRequest
from dispute_agent.modules.reasoning.llm.briefing_view import build_view
from dispute_agent.modules.reasoning.llm.reasoner import check, to_proposal
from dispute_agent.modules.reasoning.templates import neutralise

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from dispute_agent.kernel.domain.reasoning import (
        CaseBriefing,
        ModelResponse,
        Proposal,
        RenderedPrompt,
    )
    from dispute_agent.kernel.ports import Clock, IdGenerator, ModelGateway, PromptRegistry
    from dispute_agent.modules.reasoning.llm.briefing_view import BriefingView

ADAPTER = "agent"
PROMPT = "case_investigator_agent"
MAX_TURNS = 4
MAX_RESULT_CHARS = 2500
TOOLS = ("open_evidence", "policy_lookup", "check_draft")


@dataclass(frozen=True)
class Note:
    """One step of the investigation, shown to the model on the next turn."""

    step: int
    tool: str
    argument: str
    result: str
    untrusted: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "tool": self.tool,
            "argument": self.argument,
            "result": self.result,
            "untrusted": self.untrusted,
        }


@dataclass
class AgenticReasoner:
    """Implements Reasoner. Read-only tools in a bounded loop through the guarded gateway."""

    prompts: PromptRegistry
    gateway: ModelGateway
    ids: IdGenerator
    clock: Clock
    version: str = "v1"
    max_output_tokens: int = 4000
    #: `query -> [(source, text)]`. The local policy index by default; injected in tests.
    policy_search: Callable[[str], list[tuple[str, str]]] | None = None
    #: Every move the last case took, for tests and the eval report (never logged).
    trace: list[str] = field(default_factory=list)

    def propose(self, briefing: CaseBriefing) -> Proposal:
        view = build_view(briefing)
        notes: list[Note] = []
        self.trace = []
        for turn in range(1, MAX_TURNS + 1):
            last_turn = turn == MAX_TURNS
            response, rendered = self._ask(briefing, view, notes, must_decide=last_turn)
            step, argument, draft = _parse(response.output)
            self.trace.append(step)
            if step == "decide" or last_turn:
                try:
                    check(draft, briefing, view)
                except InvalidModelOutput as problem:
                    if last_turn:
                        raise
                    # Not a failure yet: the agent sees what is wrong and gets another turn.
                    notes.append(Note(turn, "decide", "", f"rejected: {problem.args[0]}"))
                    continue
                return self._proposal(draft, response, briefing, rendered)
            notes.append(self._run(turn, step, argument, draft, briefing, view))
        raise InvalidModelOutput("the investigation ended without a decision")

    # -- one model turn --------------------------------------------------------------------------

    def _ask(
        self, briefing: CaseBriefing, view: BriefingView, notes: list[Note], *, must_decide: bool
    ) -> tuple[ModelResponse, RenderedPrompt]:
        variables: dict[str, Any] = {
            "b": view.b,
            "notes": [n.as_json() for n in notes],
            "must_decide": "true" if must_decide else "false",
        }
        rendered = self.prompts.load(PROMPT, self.version, variables, view.untrusted)
        try:
            response = self.gateway.complete(
                ModelRequest(
                    case_id=briefing.case_id,
                    purpose="case_investigator",
                    prompt=rendered,
                    max_output_tokens=self.max_output_tokens,
                )
            )
            return response, rendered
        except (TransientPortError, ModuleUnavailable):
            if notes:
                # The investigation had started; a model that stops answering mid-way is not the
                # same as one that was never reachable, so the case goes to a person.
                raise InvalidModelOutput("the model stopped answering mid-investigation") from None
            raise

    # -- the tools (read-only: the briefing in memory, and the local policy index) ---------------

    def _run(
        self,
        turn: int,
        step: str,
        argument: str,
        draft: Mapping[str, Any],
        briefing: CaseBriefing,
        view: BriefingView,
    ) -> Note:
        if step == "open_evidence":
            return self._open(turn, argument, briefing)
        if step == "policy_lookup":
            passages = self._search(argument)
            text = "\n".join(f"[{src}] {body}" for src, body in passages) or "no passages found"
            return Note(turn, step, argument, text[:MAX_RESULT_CHARS])
        try:
            check(draft, briefing, view)
            return Note(turn, "check_draft", "", "no problems found")
        except InvalidModelOutput as problem:
            return Note(turn, "check_draft", "", f"problem: {problem.args[0]}")

    def _open(self, turn: int, evidence_id: str, briefing: CaseBriefing) -> Note:
        for item in briefing.evidence.items:
            texts = {f"{item.id}.{n}": t for n, t in enumerate(item.result.untrusted)}
            if evidence_id in texts:
                text = neutralise(texts[evidence_id].text)[:MAX_RESULT_CHARS]
                return Note(turn, "open_evidence", evidence_id, text, untrusted=True)
            if str(item.id) != evidence_id:
                continue
            value = json.dumps(item.result.value, sort_keys=True, default=str)
            if item.provenance is Provenance.SELLER:
                return Note(
                    turn,
                    "open_evidence",
                    evidence_id,
                    neutralise(value)[:MAX_RESULT_CHARS],
                    untrusted=True,
                )
            status = item.result.status.value
            return Note(
                turn,
                "open_evidence",
                evidence_id,
                f"status {status}; value {value}"[:MAX_RESULT_CHARS],
            )
        return Note(turn, "open_evidence", evidence_id, "no evidence item with that id")

    def _search(self, query: str) -> list[tuple[str, str]]:
        if self.policy_search is not None:
            return self.policy_search(query)
        from dispute_agent.modules.reasoning.explainer.policy_index import default_index

        return [(p.source, p.text[:600]) for p in default_index().search(query, 3)]

    # -- the decision -------------------------------------------------------------------------------

    def _proposal(
        self,
        draft: Mapping[str, Any],
        response: ModelResponse,
        briefing: CaseBriefing,
        rendered: RenderedPrompt,
    ) -> Proposal:
        decided = response.model_copy(update={"output": dict(draft)})
        # As the single-call investigator: the briefing hash is over the message the model saw
        # when it decided, so the decision can be reproduced exactly.
        return to_proposal(
            decided,
            briefing,
            briefing_hash=payload_hash(rendered.messages[-1].content),
            prompt_name=PROMPT,
            prompt_version=self.version,
            prompt_hash=rendered.hash,
            ids=self.ids,
            clock=self.clock,
        )


def _parse(output: Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    step = str(output.get("step", ""))
    if step not in (*TOOLS, "decide"):
        raise InvalidModelOutput(f"unknown step {step!r}")
    draft = output.get("draft")
    if not isinstance(draft, dict):
        raise InvalidModelOutput("every turn must carry a complete draft")
    return step, str(output.get("argument") or "")[:200], draft


__all__ = ["ADAPTER", "MAX_TURNS", "PROMPT", "TOOLS", "AgenticReasoner"]
