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

The cross-case hooks are present and wired to the Null adapters from P0-06, but `advance`
does not call them yet: `patterns_for` belongs in the investigate step and `record_outcome`
in the transition that records an outcome, and neither exists until Phase 2. The point of D3
is that the socket is here and the kernel will not change when Phase 7 fills it — not that
it is already in use. (The first draft of this docstring claimed it was; the Phase 1 safety
review caught the overstatement.)
"""

from __future__ import annotations

import dataclasses
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
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.enums import CauseKind
    from dispute_agent.kernel.domain.events import Event
    from dispute_agent.kernel.domain.ids import EventId, IntentId
    from dispute_agent.kernel.domain.models import Case
    from dispute_agent.kernel.domain.patterns import CaseOutcome, PatternRecord
    from dispute_agent.kernel.ports import Clock, IdGenerator, Tracer
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
    #: The plan now waiting on this case (set with the transition into AWAITING_APPROVAL or
    #: EXECUTING). The only case fields a step may set, and only through the loop, so they
    #: land in the same save as the transition and its event (I8).
    pending_intent_id: IntentId | None = None
    action_seq: int | None = None

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
        tracer: Tracer | None = None,
    ) -> None:
        self._machine = machine
        self._steps = steps
        self._clock = clock
        self._ids = ids
        self._patterns = patterns
        self._outcomes = outcomes
        self._max_steps = max_steps
        self._tracer = tracer

    def _run_traced(self, step: Step, case: Case, now: datetime) -> StepOutcome:
        """One span per step, carrying ids and states only (observability.md section 3)."""
        if self._tracer is None:
            return step.run(case, now)
        with self._tracer.span(
            f"step.{step.name}", case_id=str(case.id), state_from=case.state.value
        ):
            return step.run(case, now)

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
        """HOOK A. The Null adapter returns () until Phase 7 (D3).

        Not yet called by `advance`: this belongs in the investigate step, which Phase 2
        builds. When it is called, a failure must not be a silent skip — it becomes a
        `pattern_context_unavailable` caution signal, because not knowing whether a pattern
        applies is a reason to be careful (I7). `kernel/policy/signals.py` already has it.
        """
        if self._patterns is None:
            return ()
        return self._patterns.patterns_for(facts)  # type: ignore[attr-defined, no-any-return]

    def record_outcome(self, outcome: CaseOutcome) -> None:
        """HOOK C. Its failure never affects the case.

        Not yet called by `advance`: it belongs with the `record_outcome` transition effect,
        which no Phase 1 step reaches.
        """
        if self._outcomes is None:
            return
        self._outcomes.on_outcome(outcome)  # type: ignore[attr-defined]

    def advance(
        self,
        case: Case,
        causation_id: EventId,
        *,
        commit: Callable[[Case, Case], Case] | None = None,
    ) -> AdvanceResult:
        """Run steps until the case rests, finishes, or the limit is reached.

        Returns the case and every event produced. Without `commit` the caller records them:
        this function decides, and keeping those apart is what lets the loop be tested
        without a database.

        **With `commit`, the case row is saved after every transition** - `commit(before,
        after)` saves `after` and returns it with its new version. That is what
        orchestrator.md means by "each step commits exactly one transition", and it is not
        optional once a case can move more than one state in a call: the database's
        `tr_cases_legal_transition` checks each saved change is one legal step, so saving
        only the end of RECEIVED -> ... -> AWAITING_APPROVAL is correctly refused as an
        illegal jump. Saving once at the end only ever worked because, until Phase 3, every
        case stopped one step from where it started.
        """
        events: list[Event] = []
        current = case
        steps_run = 0

        def landed(applied: Applied) -> Case:
            return commit(current, applied.case) if commit is not None else applied.case

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
                return AdvanceResult(landed(applied), tuple(events), steps_run, "step_limit")

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
                return AdvanceResult(landed(applied), tuple(events), steps_run, "no_step")

            now = self._clock.now()
            try:
                outcome = self._run_traced(step, current, now)
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
                    landed(applied), tuple(events), steps_run + 1, "module_unavailable"
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
            if outcome.pending_intent_id is not None or outcome.action_seq is not None:
                updates: dict[str, object] = {}
                if outcome.pending_intent_id is not None:
                    updates["pending_intent_id"] = outcome.pending_intent_id
                if outcome.action_seq is not None:
                    updates["action_seq"] = outcome.action_seq
                applied = dataclasses.replace(applied, case=applied.case.model_copy(update=updates))
            events.append(applied.event)
            current = landed(applied)

    # -- helpers ---------------------------------------------------------------------------

    def _escalate(self, case: Case, reason: ReviewReason, causation_id: EventId) -> Applied:
        return self._machine.apply(
            case,
            Trigger.ESCALATE,
            Cause(kind=_step_cause(), ref="orchestrator"),
            self._clock.now(),
            self._new_event_id(),
            causation_id,
            # Deliberately empty. An earlier version passed every guard in the table as
            # True, which is exactly the assumption `StateMachine.apply` refuses to make —
            # and it would have satisfied `intent_is_not_credit` and `no_verified_credit`
            # among them. It is inert today only because no escalate row carries guards, and
            # `test_no_escalate_transition_carries_guards` fails in CI the moment one does,
            # which is a better place to find out than a ConfigError in production.
            guards={},
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
