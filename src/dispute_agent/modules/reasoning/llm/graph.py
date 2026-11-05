"""A multi-step investigator on LangGraph: two independent passes, reconciled by code (ADR 0041).

The single-call investigator asks the model once. This one is a small LangGraph state graph:

    START -> first_pass -> (failed? END) -> second_pass -> reconcile -> END

- **first_pass / second_pass**: the same prompt through the same guarded `ModelGateway`, asked
  twice and independently (the inner `LlmReasoner`, with its own checks and one retry).
- **reconcile** (plain code, no model):
  - same action -> keep it, with the *lower* of the two confidences;
  - different actions -> keep the **more cautious** one (CautionRank, ADR 0025) and mark the
    confidence LOW, so R22 sends a low-confidence credit or uphold to a person. A model that
    disagrees with itself is not trusted to decide.
  - the second pass failed -> keep the first, with LOW confidence.

It changes nothing about who decides: it still returns one `Proposal`, which policy judges as
always (I1). It never calls a LangChain model - every node is a plain function and every model
call goes through our gateway, so spending caps, logging and failover all still apply (I14).
It costs two calls per round instead of one; it is off unless `REASONING_ENGINE=graph`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from dispute_agent.kernel.domain.caution_rank import CONFIDENCE_ORDER, rank
from dispute_agent.kernel.domain.enums import Confidence
from dispute_agent.kernel.domain.errors import InvalidModelOutput

# Imported at runtime on purpose: LangGraph reads the state's type hints when it runs.
from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal  # noqa: TC001

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import Reasoner

ADAPTER = "graph"
#: Two passes and a reconcile: nothing loops, so the limit only guards against a wiring error.
RECURSION_LIMIT = 8


class GraphState(TypedDict, total=False):
    briefing: CaseBriefing
    first: Proposal
    second: Proposal
    second_failed: bool
    result: Proposal
    note: str


@dataclass
class GraphReasoner:
    """Implements Reasoner, around the single-call reasoner, as a LangGraph state graph."""

    inner: Reasoner
    _graph: Any = None

    def propose(self, briefing: CaseBriefing) -> Proposal:
        state = self._compiled().invoke(
            {"briefing": briefing}, {"recursion_limit": RECURSION_LIMIT}
        )
        return state["result"]  # type: ignore[no-any-return]

    def _compiled(self) -> Any:
        if self._graph is None:
            from langgraph.graph import END, START, StateGraph

            graph = StateGraph(GraphState)
            graph.add_node("first_pass", self._first_pass)
            graph.add_node("second_pass", self._second_pass)
            graph.add_node("reconcile", self._reconcile)
            graph.add_edge(START, "first_pass")
            graph.add_edge("first_pass", "second_pass")
            graph.add_edge("second_pass", "reconcile")
            graph.add_edge("reconcile", END)
            self._graph = graph.compile()
        return self._graph

    # -- nodes (plain functions; errors from the first pass propagate exactly as before) ------

    def _first_pass(self, state: GraphState) -> GraphState:
        return {"first": self.inner.propose(state["briefing"])}

    def _second_pass(self, state: GraphState) -> GraphState:
        try:
            return {"second": self.inner.propose(state["briefing"])}
        except InvalidModelOutput:
            # The first answer stands, but with no second opinion it is not a confident one.
            return {"second_failed": True}

    def _reconcile(self, state: GraphState) -> GraphState:
        first = state["first"]
        second = state.get("second")
        if second is None:
            return {"result": _lowered(first), "note": "second pass failed"}
        if second.action is first.action:
            lower = min(first.confidence, second.confidence, key=lambda c: CONFIDENCE_ORDER[c])
            return {"result": first.model_copy(update={"confidence": lower}), "note": "agreed"}
        cautious = second if rank(second.action) > rank(first.action) else first
        note = (
            f"passes disagreed ({first.action.value} vs {second.action.value}); more cautious kept"
        )
        return {"result": _lowered(cautious), "note": note}


def _lowered(proposal: Proposal) -> Proposal:
    return proposal.model_copy(update={"confidence": Confidence.LOW})


__all__ = ["ADAPTER", "RECURSION_LIMIT", "GraphReasoner"]
