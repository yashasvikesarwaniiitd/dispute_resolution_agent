"""A fake Jira that is as unhelpful as the real one, in the two ways that matter.

**It has no idempotency key on create.** Jira's create endpoint takes nothing that would let
a retry be recognised, so this fake models `idempotent_writes = False` honestly: call it
twice with the same key and you get two issues. That is not a bug in the fake — it is the
fact the executor's design exists to survive (ADR 0020). The executor never retries a create
blind; it reads its own action record first.

**Its search is eventually consistent.** `find_issue` can return `None` for an issue that
exists, and this fake does exactly that for a configurable window. A miss is therefore never
proof of absence (integrations.md section 2.2), and a fake with an instantly-consistent
search would let code that treats a miss as "safe to create" pass its tests.

The marker property is set in a second call after create, so the gap between them can be
tested: the issue exists, the marker does not, and the only honest answer is
`AmbiguousOutcome` carrying the issue key.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    AuthPortError,
    ExternalNotFound,
    ModuleUnavailable,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import IssueRef, IssueSnapshot

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.kernel.domain.external import IssueSpec, OpsFinding
    from dispute_agent.kernel.domain.ids import IdempotencyKey

MODULE = "ticketing"
ADAPTER = "fake"

#: Jira's own words are "a few seconds to minutes". Short here, but never zero: a search
#: that is instantly consistent would make "a miss is not proof of absence" untestable.
SEARCH_CONSISTENCY_DELAY = timedelta(seconds=2)

AGENT_LABEL = "dispute-agent"


def key_label(key: IdempotencyKey) -> str:
    """`wd1-<first 40 hex>`: the searchable marker. Labels cannot contain spaces."""
    digest = str(key).split(":")[-1]
    return f"wd1-{digest[:40]}"


def normalise(text: str) -> str:
    """Jira restructures ADF on storage, so the comparison is on text (section 2.3)."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass(frozen=True)
class StoredIssue:
    ref: IssueRef
    spec: IssueSpec
    key: IdempotencyKey | None
    created_at: datetime
    status: str = "To Do"
    resolution: str | None = None


@dataclass
class FakeTicketingSystem:
    """Implements TicketingSystem.

    `idempotent_writes` is False and must stay False. Setting it True would be a lie about
    Jira, and the executor reads that flag to decide whether a retry is safe.
    """

    project_key: str = "WD"
    idempotent_writes: bool = False
    lookup_supported: bool = True
    faults: dict[str, str] = field(default_factory=dict)
    now: datetime | None = None

    _issues: list[StoredIssue] = field(default_factory=list)
    _findings: list[OpsFinding] = field(default_factory=list)
    _create_calls: int = 0

    # -- writing ---------------------------------------------------------------------------

    def upsert_issue(self, spec: IssueSpec, key: IdempotencyKey) -> IssueRef:
        """Create, then mark. Two calls, because Jira makes them two calls."""
        self._create_calls += 1
        self._check_module()
        fault = self.faults.get("upsert_issue")

        if fault == "auth":
            raise AuthPortError("the Jira credentials are no longer valid")
        if fault == "permanent":
            # S12c: a 400 from Jira. Never retried; the case goes to a person.
            raise PermanentPortError("Jira refused the issue", status=400)
        if fault == "transient":
            raise TransientPortError("Jira did not respond in time")

        moment = self._moment()
        number = len(self._issues) + 1
        ref = IssueRef(key=f"{spec.project_key}-{number}", id=str(1000 + number))

        if fault == "marker_fails":
            # The issue exists and the marker does not. Reporting success would lose it;
            # reporting failure would invite a second create. Ambiguous is the truth, and
            # the key goes with it so a person can find the issue (section 2.2).
            self._issues.append(StoredIssue(ref, spec, None, moment))
            raise AmbiguousOutcome(
                "the issue was created but could not be marked", external_ref=ref.key
            )

        self._issues.append(StoredIssue(ref, spec, key, moment))
        return ref

    # -- looking up --------------------------------------------------------------------------

    def find_issue(self, key: IdempotencyKey) -> IssueRef | None:
        """Best effort by label. A hit is reliable; a miss is not proof of absence."""
        self._check_module()
        if self.faults.get("find_issue") == "transient":
            raise TransientPortError("the search could not be completed")

        wanted = key_label(key)
        for issue in self._issues:
            if issue.key is None or key_label(issue.key) != wanted:
                continue
            if self._moment() < issue.created_at + SEARCH_CONSISTENCY_DELAY:
                # Indexed, eventually. Right now the search genuinely cannot see it, which
                # is the behaviour that makes "a miss means nothing" true.
                return None
            return issue.ref
        return None

    def get_issue(self, ref: IssueRef) -> IssueSnapshot:
        """Read-back by key, which the docs do not call eventually consistent."""
        self._check_module()
        issue = next((i for i in self._issues if i.ref.key == ref.key), None)
        if issue is None:
            raise ExternalNotFound(f"no issue {ref.key}")
        return IssueSnapshot(
            key=issue.ref.key,
            project_key=issue.spec.project_key,
            labels=(AGENT_LABEL, key_label(issue.key)) if issue.key else (AGENT_LABEL,),
            summary=issue.spec.summary,
            # Text, not ADF: Jira may restructure the document on storage (section 2.3).
            description_text=normalise(issue.spec.description),
            status=issue.status,
            resolution=issue.resolution,
            idempotency_key=issue.key,
        )

    def fetch_findings(self, since: datetime) -> Sequence[OpsFinding]:
        self._check_module()
        if self.faults.get("fetch_findings") == "transient":
            raise TransientPortError("findings could not be read")
        return tuple(f for f in self._findings if f.at > since)

    # -- test affordances ---------------------------------------------------------------------

    def post_finding(self, finding: OpsFinding) -> None:
        """Ops records a finding. Tests only."""
        self._findings.append(finding)

    def resolve(self, key: str, resolution: str) -> None:
        """Mark an issue resolved, for the duplicate-resolution check in verification."""
        self._issues = [
            StoredIssue(i.ref, i.spec, i.key, i.created_at, "Done", resolution)
            if i.ref.key == key
            else i
            for i in self._issues
        ]

    def issue_count(self) -> int:
        """How many issues exist. The duplicate-side-effect gate reads this, not a receipt."""
        return len(self._issues)

    def create_attempts(self) -> int:
        return self._create_calls

    # -- helpers ------------------------------------------------------------------------------

    def _check_module(self) -> None:
        if self.faults.get("module") == "unavailable":
            raise ModuleUnavailable(MODULE)

    def _moment(self) -> datetime:
        return self.now or datetime.now(UTC)


__all__ = [
    "ADAPTER",
    "AGENT_LABEL",
    "MODULE",
    "SEARCH_CONSISTENCY_DELAY",
    "FakeTicketingSystem",
    "key_label",
    "normalise",
]
