"""The confirmation rule (P7-08, I7, ADR 0037).

A pattern's status moves **only on independent evidence**: a structured operations finding on
its own Jira investigation. Nothing else can move it - not the number of disputes, not how the
cases ended, not a model's hypothesis.

Why outcomes never count: a pattern makes the agent cautious; caution produces escalations and
human reviews; those outcomes would then look like evidence for the pattern. That loop would
confirm any pattern that was ever shown, true or not. So `apply` takes a finding and nothing
else, and `independent_support` - reported, never acted on - leaves out every case that could
have been shown the pattern (`informed` links, recorded conservatively by the observer).

Only structured fields are read, never the finding's note (I5): an injected note saying "this
proves fraud" is text, and text cannot move a status.

| Finding | From | To |
|---------|------|----|
| `no_fault_found` | UNCONFIRMED | DISMISSED |
| `inspected`, `cause_confirmed: true` | UNCONFIRMED, DISMISSED | CONFIRMED |
| `inspected`, `fix_deployed: true` | UNCONFIRMED, CONFIRMED | FIXED_MONITORING (fixed_at = the finding's time) |

Anything else is recorded and changes nothing: a confirmed cause is not undone by one
inspection that found no fault - a person decides that.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime

    from dispute_agent.kernel.domain.external import OpsFinding
    from dispute_agent.modules.cross_case.sql_context.repository import PatternRepository

_MOVES: dict[str, dict[str, str]] = {
    "no_fault_found": {"UNCONFIRMED": "DISMISSED"},
    "fault_confirmed": {"UNCONFIRMED": "CONFIRMED", "DISMISSED": "CONFIRMED"},
    "fix_deployed": {"UNCONFIRMED": "FIXED_MONITORING", "CONFIRMED": "FIXED_MONITORING"},
}


def verdict_of(finding: OpsFinding) -> str | None:
    """The structured verdict, or None. The note is never read (I5)."""
    if finding.kind == "no_fault_found":
        return "no_fault_found"
    if finding.kind == "inspected":
        if finding.measured.get("fix_deployed") is True:
            return "fix_deployed"
        if finding.measured.get("cause_confirmed") is True:
            return "fault_confirmed"
    return None


@dataclass
class ConfirmationRule:
    repository: PatternRepository

    def apply(
        self, pattern_id: str, finding_ref: str, verdict: str, at: datetime, now: datetime
    ) -> str:
        """Record an ops finding and move the status if the table above says so."""
        if not self.repository.record_finding(pattern_id, finding_ref, verdict, now):
            return self.repository.get(pattern_id).status  # seen before: idempotent
        current = self.repository.get(pattern_id).status
        target = _MOVES.get(verdict, {}).get(current)
        if target is None:
            return current
        self.repository.set_status(
            pattern_id,
            target,
            f"ops:{verdict}",
            now,
            evidence_ref=finding_ref,
            fixed_at=at if target == "FIXED_MONITORING" else None,
        )
        return target

    def independent_support(self, pattern_id: str) -> int:
        """Member cases that could not have been shown the pattern. Reported, never acted on."""
        informed = set(self.repository.linked(pattern_id, "informed"))
        return sum(
            1 for case in self.repository.linked(pattern_id, "member") if case not in informed
        )


@dataclass
class FindingsReader:
    """Reads ops findings and applies the ones on a pattern's investigation issue.

    `findings` is the ticketing system's read method (fetch_findings), injected as a function
    so this class holds no object that can write (I2, `test_analyzer_never_writes.py`).
    """

    rule: ConfirmationRule
    findings: Callable[[datetime], Sequence[OpsFinding]]
    #: The verified investigation issue key for a pattern, or None.
    issue_key: Callable[[str], str | None]

    def run(self, since: datetime, now: datetime) -> list[tuple[str, str, str]]:
        """(pattern_id, verdict, new status) for every finding applied."""
        by_issue: dict[str, str] = {}
        for pattern in self.rule.repository.all():
            key = self.issue_key(pattern.id)
            if key:
                by_issue[key] = pattern.id
        applied: list[tuple[str, str, str]] = []
        for finding in self.findings(since):
            pattern_id = by_issue.get(finding.issue_key)
            verdict = verdict_of(finding)
            if pattern_id is None or verdict is None:
                continue
            status = self.rule.apply(pattern_id, finding.finding_id, verdict, finding.at, now)
            applied.append((pattern_id, verdict, status))
        return applied
