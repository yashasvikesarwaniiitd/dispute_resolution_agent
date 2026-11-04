"""What a scenario declared, against what the run did (release-gates.yaml, fixture suite gate).

Every scenario is checked on every field it declares: its path (`path`, `path_suffix` or
`path_contains`), its final state, its review code(s), and its counts. A scenario that declares
none of these is **under-specified** and fails - the gate must never pass by skipping a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dispute_agent.kernel.domain.enums import CaseState

WAITING = {"AWAITING_SELLER", "AWAITING_OPS", "AWAITING_APPROVAL"}


@dataclass
class Outcome:
    """What one scripted run produced (filled by the runner)."""

    path: list[str] = field(default_factory=list)
    final: str | None = None
    #: The review code (rendered, e.g. "module_unavailable:evidence") at the end, if any.
    review: str | None = None
    #: The review code the first time the case entered NEEDS_HUMAN_REVIEW, if it did.
    first_review: str | None = None
    counts: dict[str, int] = field(default_factory=dict)
    #: The proposal actions the investigator made, in order (from the proposals store).
    actions: list[str] = field(default_factory=list)
    error: str | None = None
    #: What the hard gates and the metrics count (filled by the runner; see metrics.py).
    unauthorised_credits: int = 0
    duplicate_side_effects: int = 0
    unknown_actions: int = 0
    content_leaks: int = 0
    #: (route, status, latency_ms, cost_usd_micros) per model call.
    model_calls: list[tuple[str, str, int, int]] = field(default_factory=list)
    #: Required evidence items FOUND and required in the first round (completeness).
    evidence_found: int = 0
    evidence_required: int = 0
    #: Hours from opening to a RESOLVED_* state, on the fake clock.
    resolution_hours: float | None = None


@dataclass
class Result:
    scenario_id: str
    outcome: Outcome
    mismatches: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches


def check(scenario_id: str, expect: dict[str, Any], outcome: Outcome) -> Result:
    result = Result(scenario_id, outcome)
    miss = result.mismatches
    if outcome.error:
        miss.append(f"run error: {outcome.error}")
        return result

    declared = False
    if expect.get("path"):
        declared = True
        if outcome.path != list(expect["path"]):
            miss.append(f"path {outcome.path} != {expect['path']}")
    if expect.get("path_suffix"):
        declared = True
        suffix = list(expect["path_suffix"])
        if outcome.path[-len(suffix) :] != suffix:
            miss.append(f"path ends {outcome.path[-len(suffix) :]} != {suffix}")
    if expect.get("path_contains"):
        declared = True
        if not _in_order(outcome.path, list(expect["path_contains"])):
            miss.append(f"path {outcome.path} lacks {expect['path_contains']} in order")
    if expect.get("final") is not None:
        declared = True
        if outcome.final != str(expect["final"]):
            miss.append(f"final {outcome.final} != {expect['final']}")
    if expect.get("review"):
        declared = True
        if outcome.review != str(expect["review"]) and outcome.first_review != str(
            expect["review"]
        ):
            miss.append(f"review {outcome.review or outcome.first_review} != {expect['review']}")
    if expect.get("review_on_first_stop"):
        declared = True
        if outcome.first_review != str(expect["review_on_first_stop"]):
            miss.append(f"first review {outcome.first_review} != {expect['review_on_first_stop']}")
    for name, wanted in (expect.get("counts") or {}).items():
        declared = True
        have = outcome.counts.get(str(name))
        if have is None:
            miss.append(f"count {name} not measured")
        elif not _count_ok(have, wanted):
            miss.append(f"count {name} = {have}, expected {wanted}")
    if not declared:
        miss.append("under-specified: declares no path, final, review or counts")
    return result


def is_outcome(final: object) -> bool:
    """A terminal state (or NEEDS_HUMAN_REVIEW) is an outcome; a waiting state is not."""
    return isinstance(final, str) and final not in WAITING and final in CaseState.__members__


def _in_order(path: list[str], wanted: list[str]) -> bool:
    position = 0
    for state in path:
        if position < len(wanted) and state == wanted[position]:
            position += 1
    return position == len(wanted)


def _count_ok(have: int, wanted: object) -> bool:
    if isinstance(wanted, str) and wanted.startswith(">="):
        return have >= int(wanted[2:])
    return have == int(str(wanted))


__all__ = ["Outcome", "Result", "check", "is_outcome"]
