"""Two-pass anchoring control (P4-08, ADR 0025, agent-design.md section 7, I7).

When a briefing carries cross-case patterns, the model decides twice: pass 1 without the
patterns (an independent view), pass 2 with them, shown pass 1's action and confidence. The
result may be *more* cautious than pass 1, never less:

- if pass 2's action ranks below pass 1's (say credit after uphold), pass 1 is returned;
- if pass 2 keeps the action but raises the confidence or changes the parameters, pass 1's
  confidence and parameters are kept - otherwise a pattern could lift a proposal out of
  R22's low-confidence escalation without changing the action at all.

Either way the result's `pattern_influence.note` starts with `pattern_relaxation_blocked`,
so an attempt to relax a case is visible on the stored proposal. The model's own
`changed_recommendation` is never the control: the comparison is made here, in code.

With no patterns this is a single pass. Phase 7 (P7-06) connects it: app.py wraps the LLM
reasoner in this class when `cross_case` is on, and raises the per-case call limit to
`llm.max_calls_per_case_with_patterns` (10), since a patterned round costs two calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.caution_rank import CONFIDENCE_ORDER, rank
from dispute_agent.kernel.domain.reasoning import IndependentView, PatternInfluence

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.kernel.ports import Reasoner

BLOCKED = "pattern_relaxation_blocked"


@dataclass
class TwoPassReasoner:
    """Implements Reasoner, around any single-pass reasoner."""

    inner: Reasoner
    blocked: list[str] = field(default_factory=list)

    def propose(self, briefing: CaseBriefing) -> Proposal:
        if not briefing.pattern_context:
            return self.inner.propose(briefing)
        first = self.inner.propose(briefing.model_copy(update={"pattern_context": ()}))
        second = self.inner.propose(
            briefing.model_copy(
                update={
                    "independent_view": IndependentView(
                        action=first.action, confidence=first.confidence
                    )
                }
            )
        )
        return self.choose(first, second)

    def choose(self, first: Proposal, second: Proposal) -> Proposal:
        if rank(second.action) < rank(first.action):
            return self._blocked(first, f"{second.action.value} after {first.action.value}")
        if second.action is first.action and (
            CONFIDENCE_ORDER[second.confidence] > CONFIDENCE_ORDER[first.confidence]
            or second.action_parameters != first.action_parameters
        ):
            kept = second.model_copy(
                update={
                    "confidence": first.confidence,
                    "action_parameters": first.action_parameters,
                }
            )
            return self._blocked(kept, "confidence or parameters raised by a pattern")
        return self._accepted(first, second)

    def _accepted(self, first: Proposal, second: Proposal) -> Proposal:
        """Pass 2 stands. Whether the pattern changed it is decided here, by comparison
        (P7-06): a more cautious action is a change, which the kernel turns into a
        `pattern_changed_recommendation` signal (approval at least). The model's own claim
        about its influence is not trusted either way."""
        changed = rank(second.action) > rank(first.action)
        note = (
            f"pattern_changed: {first.action.value} -> {second.action.value}"
            if changed
            else "pattern_considered: same action as the independent view"
        )
        influence = PatternInfluence(considered=True, changed_recommendation=changed, note=note)
        return second.model_copy(update={"pattern_influence": influence})

    def _blocked(self, proposal: Proposal, why: str) -> Proposal:
        self.blocked.append(why)
        note = f"{BLOCKED}: {why}"[:300]
        influence = PatternInfluence(
            considered=True,
            changed_recommendation=False,
            note=note,
        )
        return proposal.model_copy(update={"pattern_influence": influence})


__all__ = ["BLOCKED", "TwoPassReasoner"]
