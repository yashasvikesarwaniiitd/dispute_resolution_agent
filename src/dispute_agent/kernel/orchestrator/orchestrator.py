"""The step loop (orchestrator.md section 3, ADR 0004, D3, I6).

A case advances one step at a time, and each step commits exactly one transition with all its
events in one transaction. A crash therefore loses at most one uncommitted step, which is
safe to run again because external writes carry idempotency keys.

Two things live here and nowhere else.

**`ModuleUnavailable` becomes `NEEDS_HUMAN_REVIEW` in one place.** Every step can raise it,
and if each handled it there would eventually be one that handled it slightly differently —
or forgot. One conversion point is what makes I6 a property of the loop rather than a habit
of nine steps.

**The step limit.** A case that keeps producing work without resting is a bug, and the
honest response is to stop it and tell someone rather than let it run. Twelve steps
(`cases.max_steps_per_advance`), then `step_limit`.

The three cross-case hooks are wired to the Null adapters from P0-06. They are called on
every run, which is the point of D3: Phase 7 changes an adapter, not this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from dispute_agent.kernel.domain.enums import TERMINAL_STATES as _TERMINAL
from dispute_agent.kernel.domain.enums import (
    CaseState,
    ReviewCode,
    Trigger,
)
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.models import Cause, ReviewReason

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.enums import CauseKind
    from dispute_agent.kernel.domain.events import Event
    from dispute_agent.kernel.domain.ids import EventId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.patterns import CaseOutcome, PatternRecord
    from dispute_agent.kernel.ports import Clock, IdGenerator
    from dispute_agent.kernel.statemachine.machine import Applied, StateMachine

#: States the loop will keep working through. Anything else rests or is finished.
AUTO_STATES = (
    CaseState.RECEIVED,
    CaseState.GATHERING_EVIDENCE,
    CaseState.CALCULATING,
    CaseState.INVESTIGATING,
    CaseState.EXECUTING,
    CaseState.VERIFYING,
)


@dataclass(frozen=True)
class StepOutcome:
    """What a step decided. `rest` means "nothing more to do now", not "finished"."""

    trigger: Trigger | None = None
    review_reason: ReviewReason | None = None
    guards: dict[str, bool] = field(default_factory=dict)
    detail: str = ""

    @property
    def rests(self) -> bool:
        return self.trigger is None


class Step(Protocol):
    """One unit of work on a case. Pure of persistence: it decides, the loop records."""

    name: str

    def run(self, case: Case, now: datetime) -> StepOutcome: ...


@dataclass(frozen=True)
class AdvanceResult:
    case: Case
    events: tuple[Event, ...]
    steps_run: int
    stopped_because: str


class EscalatingStep:
    """A step that always sends the case to a person, naming why.

    Phase 1 has no domain pack and no reasoner, so `eligibility`, `assess` and `investigate`
    cannot decide anything. Escalating with a reason that names the phase is honest; guessing
    an outcome would be the single worst thing a step could do here.
    """

    def __init__(
        self, name: str, code: ReviewCode, detail: str, *, module: str | None = None
    ) -> None:
        self.name = name
        self._code = code
        self._detail = detail
        self._module = module

    def run(self, case: Case, now: datetime) -> StepOutcome:
        return StepOutcome(
            trigger=Trigger.ESCALATE,
            review_reason=ReviewReason(code=self._code, module=self._module, detail=self._detail),
            guards={},
            detail=self._detail,
        )


class Orchestrator:
    """Advances one case until it rests, finishes, or hits the step limit."""

    def __init__(
        self,
        machine: StateMachine,
        steps: dict[str, Step],
        clock: Clock,
        ids: IdGenerator,
        *,
        patterns: object | None = None,
        outcomes: object | None = None,
        max_steps: int = 12,
    ) -> None:
        self._machine = machine
        self._steps = steps
        self._clock = clock
        self._ids = ids
        self._patterns = patterns
        self._outcomes = outcomes
        self._max_steps = max_steps

    def step_for(self, state: CaseState) -> Step | None:
        """Which step runs in this state, from the table's `steps` block."""
        for name, body in self._machine.table.steps.items():
            # `steps` comes from YAML, so the shape is checked rather than assumed. A step
            # block without an `in` list would silently never match, which is the kind of
            # typo that looks like "the step just does not run".
            states = body.get("in") if isinstance(body, dict) else None
            if isinstance(states, list) and state.value in states:
                return self._steps.get(name)
        return None

    def patterns_for(self, facts: CaseFacts) -> Sequence[PatternRecord]:
        """HOOK A. Called on every run; the Null adapter returns () until Phase 7 (D3).

        A failure here is not a silent skip — the caller turns it into a caution signal,
        because not knowing whether a pattern applies is a reason to be careful (I7).
        """
        if self._patterns is None:
            return ()
        return self._patterns.patterns_for(facts)  # type: ignore[attr-defined, no-any-return]

    def record_outcome(self, outcome: CaseOutcome) -> None:
        """HOOK C. Its failure never affects the case."""
        if self._outcomes is None:
            return
        self._outcomes.on_outcome(outcome)  # type: ignore[attr-defined]

    def advance(self, case: Case, causation_id: EventId) -> AdvanceResult:
        """Run steps until the case rests, finishes, or the limit is reached.

        Returns the case and every event produced. The caller commits them: this function
        decides, and keeping those apart is what lets the whole loop be tested without a
        database.
        """
        events: list[Event] = []
        current = case
        steps_run = 0

        while True:
            if current.state in _TERMINAL:
                return AdvanceResult(current, tuple(events), steps_run, "terminal")
            if current.state not in AUTO_STATES:
                return AdvanceResult(current, tuple(events), steps_run, "waiting")

            if steps_run >= self._max_steps:
                # A case producing work without resting is a bug. Stopping and saying so
                # beats letting it run (orchestrator.md section 3).
                applied = self._escalate(
                    current,
                    ReviewReason(
                        code=ReviewCode.STEP_LIMIT,
                        detail=f"{self._max_steps} steps without resting",
                    ),
                    causation_id,
                )
                events.append(applied.event)
                return AdvanceResult(applied.case, tuple(events), steps_run, "step_limit")

            step = self.step_for(current.state)
            if step is None:
                applied = self._escalate(
                    current,
                    ReviewReason(
                        code=ReviewCode.INTERNAL_ERROR,
                        detail=f"no step for {current.state.value}",
                    ),
                    causation_id,
                )
                events.append(applied.event)
                return AdvanceResult(applied.case, tuple(events), steps_run, "no_step")

            now = self._clock.now()
            try:
                outcome = step.run(current, now)
            except ModuleUnavailable as unavailable:
                # The one conversion point (I6). Nine steps each handling this would
                # eventually be eight handling it and one forgetting.
                applied = self._escalate(
                    current,
                    ReviewReason(
                        code=ReviewCode.MODULE_UNAVAILABLE,
                        module=unavailable.module,
                        detail=f"{step.name} needed {unavailable.module}",
                    ),
                    causation_id,
                )
                events.append(applied.event)
                return AdvanceResult(
                    applied.case, tuple(events), steps_run + 1, "module_unavailable"
                )

            steps_run += 1

            if outcome.rests:
                return AdvanceResult(current, tuple(events), steps_run, "rest")

            applied = self._machine.apply(
                current,
                outcome.trigger,  # type: ignore[arg-type]
                Cause(kind=_step_cause(), ref=step.name),
                now,
                self._new_event_id(),
                causation_id,
                guards=outcome.guards,
                review_reason=outcome.review_reason,
            )
            events.append(applied.event)
            current = applied.case

    # -- helpers ---------------------------------------------------------------------------

    def _escalate(self, case: Case, reason: ReviewReason, causation_id: EventId) -> Applied:
        return self._machine.apply(
            case,
            Trigger.ESCALATE,
            Cause(kind=_step_cause(), ref="orchestrator"),
            self._clock.now(),
            self._new_event_id(),
            causation_id,
            guards=dict.fromkeys(self._machine.table.guards, True),
            review_reason=reason,
        )

    def _new_event_id(self) -> EventId:
        from dispute_agent.kernel.domain.ids import EventId as _EventId

        return _EventId(self._ids.new_id("evt"))


def _step_cause() -> CauseKind:
    from dispute_agent.kernel.domain.enums import CauseKind as _CauseKind

    return _CauseKind.STEP


def phase_1_steps() -> dict[str, Step]:
    """What Phase 1 can actually do.

    Nothing yet decides a dispute: there is no pack to calculate with (Phase 2) and no
    reasoner to investigate with (Phase 4). Each of these escalates with a reason naming the
    phase that will replace it, so a Phase 1 run reaches a person rather than pretending.
    """
    return {
        "eligibility": EscalatingStep(
            "eligibility",
            ReviewCode.ELIGIBILITY_UNKNOWN,
            "no domain pack until Phase 2 (card P2-03)",
        ),
        "gather_evidence": EscalatingStep(
            "gather_evidence",
            ReviewCode.MODULE_UNAVAILABLE,
            "no evidence sources until Phase 2 (card P2-04)",
            module="evidence",
        ),
        "assess": EscalatingStep(
            "assess", ReviewCode.ELIGIBILITY_UNKNOWN, "no calculator until Phase 2 (P2-02)"
        ),
        "investigate": EscalatingStep(
            "investigate",
            ReviewCode.PROPOSAL_ESCALATED,
            "no reasoner until Phase 2 (card P2-07)",
        ),
    }
