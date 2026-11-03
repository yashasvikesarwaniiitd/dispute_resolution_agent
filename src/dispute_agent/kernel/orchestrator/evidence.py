"""The gather round (orchestrator.md section 3, `transitions.yaml` steps.gather_evidence, D41, I4).

One round asks every planned source for one requirement each and records what came back —
**including what did not come back**. That is the whole point of the file. An evidence item
that failed is written down as failed and travels to `assess` and to the reasoner as a
failure; it is never dropped, never softened into "nothing found", and never filled in with
a plausible value. I4 is not a rule this code checks at the end, it is the only thing this
code is allowed to do.

Three decisions live here.

**MISSING is not FAILED.** The source keeps them apart (P2-05) and so does this. "There is
nothing there" is a fact about the shipment and feeds the calculation; "we could not find
out" is a fact about the fetch and must not.

**A switched-off module becomes a FAILED item, not an escalation from here.** It looks like
I6 being ignored, and it is the opposite. If `gather_evidence` let `ModuleUnavailable` out,
the orchestrator's one conversion point would send the case to a person immediately and the
round would have no record of what else succeeded. Instead the item is recorded FAILED with
`module_unavailable:<name>`, and `assess` escalates on it before assessing anything — a
kernel rule, so I6 never depends on the reasoner noticing. The case still reaches a human
with the module's name; it reaches them with the evidence round written down.

**Retrying is for blips only (D41).** A required item that failed transiently arms
EVIDENCE_RETRY for 15 minutes and the step rests, at most three times in a gather round. A
permanent error is not retried at all, and neither is `module_unavailable:` — retrying a
switched-off module three times over 45 minutes delays a case that was never going to
succeed. After the last retry the round is recorded with the FAILED item and moves on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.enums import EvidenceStatus
from dispute_agent.kernel.domain.errors import ModuleUnavailable
from dispute_agent.kernel.domain.evidence import EvidenceResult
from dispute_agent.kernel.policy.config import TimersConfig

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.case import CaseFacts
    from dispute_agent.kernel.domain.evidence import EvidenceRequirement
    from dispute_agent.kernel.domain.ids import CaseId
    from dispute_agent.kernel.ports import EvidenceSource

#: Error codes worth trying again. Named by `transitions.yaml` steps.gather_evidence.
#:
#: Kept deliberately short. A code nobody has classified is not retried, which risks one
#: extra round trip to a human and never risks hammering something that is down.
TRANSIENT_ERROR_CODES = frozenset({"timeout", "unavailable"})

#: Prefix for "the module is switched off", which is never retried however transient it looks.
MODULE_UNAVAILABLE = "module_unavailable:"


def module_unavailable_code(module: str) -> str:
    """The error code `assess` reads to escalate with I6's reason."""
    return f"{MODULE_UNAVAILABLE}{module}"


def is_retryable(error_code: str | None) -> bool:
    """Whether D41 arms a retry for this failure.

    The `module_unavailable:` check comes first and wins. A disabled module can report
    anything it likes as a cause; it is still not a blip.
    """
    if error_code is None or error_code.startswith(MODULE_UNAVAILABLE):
        return False
    return error_code in TRANSIENT_ERROR_CODES


@dataclass(frozen=True)
class GatherOutcome:
    """What one round found, and whether it is worth waiting for more.

    `results` is everything, in plan order, failures included. `retry_due_at` is set only
    when the round should rest and try again; `rest_reason` names the requirement and code
    that caused it, so a case sitting in GATHERING_EVIDENCE can be explained without
    reading the timer table.
    """

    results: tuple[EvidenceResult, ...]
    retry_due_at: datetime | None = None
    rest_reason: str = ""

    @property
    def rests(self) -> bool:
        return self.retry_due_at is not None

    def failures(self) -> tuple[EvidenceResult, ...]:
        return tuple(r for r in self.results if r.status is EvidenceStatus.FAILED)

    def unavailable_modules(self) -> tuple[str, ...]:
        """Modules that were off, in plan order and without repeats.

        `assess` escalates on the first of these before assessing (I6).
        """
        seen: list[str] = []
        for result in self.failures():
            code = result.error_code or ""
            if code.startswith(MODULE_UNAVAILABLE):
                module = code.removeprefix(MODULE_UNAVAILABLE)
                if module not in seen:
                    seen.append(module)
        return tuple(seen)

    def invented_values(self) -> tuple[EvidenceResult, ...]:
        """Any result that carries a value it had no right to. Always empty.

        `EvidenceResult` already refuses to hold a value unless the status is FOUND, so this
        cannot find anything today — and that is the point of keeping it. It lets a test
        assert I4 over a whole round at once rather than one failure mode at a time, and it
        is what would start failing if that model validator were ever relaxed.
        """
        return tuple(
            result
            for result in self.results
            if result.status is not EvidenceStatus.FOUND and result.value is not None
        )


def gather(
    *,
    case_id: CaseId,
    facts: CaseFacts,
    requirements: Sequence[EvidenceRequirement],
    sources: Mapping[str, EvidenceSource],
    now: datetime,
    retries_fired_this_round: int = 0,
    timers: TimersConfig | None = None,
) -> GatherOutcome:
    """Run one gather round. Pure of persistence: it decides, the caller records.

    Keeping the writing out means the whole of D41 and I4 can be tested with a dictionary of
    sources and an integer, which is why the retry arithmetic is worth trusting.

    `retries_fired_this_round` is how many EVIDENCE_RETRY timers have already fired for the
    case's **current** gather round. Counting per round rather than per case is finding B22:
    fired timer rows outlive a round, so a case that retried three times in round 1 would
    otherwise start round 2 already at the limit and never retry a genuine blip again.
    """
    config = timers or TimersConfig()
    results: list[EvidenceResult] = []

    for requirement in requirements:
        results.append(_fetch_one(case_id, facts, requirement, sources, now))

    retry_for = _first_retryable_required(requirements, results)
    if retry_for is not None and retries_fired_this_round < config.evidence_max_retries:
        return GatherOutcome(
            results=tuple(results),
            retry_due_at=now + timedelta(minutes=config.evidence_retry_minutes),
            rest_reason=f"{retry_for.requirement_key} failed with {retry_for.error_code}",
        )

    # Either nothing is worth retrying, or the retries are spent. Record the round as it is,
    # failures and all, and let `assess` see exactly what happened.
    return GatherOutcome(results=tuple(results))


def _fetch_one(
    case_id: CaseId,
    facts: CaseFacts,
    requirement: EvidenceRequirement,
    sources: Mapping[str, EvidenceSource],
    now: datetime,
) -> EvidenceResult:
    """One requirement, one answer, no exceptions escaping.

    A source name nobody registered is treated exactly like a module that is switched off.
    It is the same situation from the case's point of view — the thing that was supposed to
    answer this cannot — and the alternative, a KeyError out of the step, would lose the
    whole round to `internal_error`.
    """
    source = sources.get(requirement.source)
    if source is None:
        return _failed(requirement, module_unavailable_code(requirement.source), now)

    try:
        result = source.fetch(case_id, facts, requirement)
    except ModuleUnavailable as unavailable:
        return _failed(requirement, module_unavailable_code(unavailable.module), now)

    # No "FOUND with nothing in it" check here on purpose: `EvidenceResult` refuses to be
    # built that way at all (a value is set exactly when the status is FOUND), so a guard
    # here would be unreachable code pretending to carry I4. The type carries it.
    return result


def _first_retryable_required(
    requirements: Sequence[EvidenceRequirement], results: Sequence[EvidenceResult]
) -> EvidenceResult | None:
    """The first required item whose failure is worth waiting out.

    Only *required* items hold the case up. An optional item that times out is recorded as
    failed and the round continues: resting 45 minutes for something the assessment does
    not need is a delay with nothing at the end of it.
    """
    required = {r.key for r in requirements if r.required}
    for result in results:
        if (
            result.status is EvidenceStatus.FAILED
            and result.requirement_key in required
            and is_retryable(result.error_code)
        ):
            return result
    return None


def _failed(requirement: EvidenceRequirement, code: str, now: datetime) -> EvidenceResult:
    return EvidenceResult(
        requirement_key=requirement.key,
        source=requirement.source,
        status=EvidenceStatus.FAILED,
        error_code=code,
        retrieved_at=now,
    )


__all__ = [
    "MODULE_UNAVAILABLE",
    "TRANSIENT_ERROR_CODES",
    "GatherOutcome",
    "gather",
    "is_retryable",
    "module_unavailable_code",
]
