"""Parent Jira investigations, through policy and the ActionExecutor (P7-05, I2, ADR 0036).

The analyzer never writes (I2 admits no exception for a background job). For each pattern it
found, this step *proposes* an `upsert_issue` and hands it to the kernel's own PolicyEngine and
ActionExecutor - the same classes, the same rules, the same kill switch, the same look-before-
write and read-back verification every case's actions get.

The executor's subject is a case, and a pattern is not one. The socket gives no case-less
action, and Phase 7 changes no kernel line, so two things stand in (ADR 0036):

- **A subject case built in memory** for the policy context and the idempotency key: the
  pattern id as its id, the newest member case's facts, never stored in `cases`. It exists so
  the rules can run; no case is touched.
- **The module's own ActionStore** (`pattern_actions`), with the executor's semantics: a claim
  that counts attempts, status stamps, and no credit, approval or message ever (the store
  answers zero and None for those, and its table refuses any action type but UPSERT_ISSUE).

Idempotent on our own record (ADR 0020): the intent - its exact payload - is written once and
reused, so a re-run plans nothing once the issue is VERIFIED, and resumes a PLANNED or SENT
action instead of planning a new one. New members are recorded as links in our tables; the
issue's text is fixed at first detection, so a re-run never creates a second issue.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, insert, or_, select, update

from dispute_agent.infra.sqlite.mapping import from_iso, to_iso
from dispute_agent.infra.sqlite.patterns import pattern_actions, pattern_investigations
from dispute_agent.infra.sqlite.tables import cases
from dispute_agent.kernel.domain.actions import Action, ActionIntent, IssuePayload
from dispute_agent.kernel.domain.case import CaseFacts
from dispute_agent.kernel.domain.enums import (
    ActionStatus,
    ActionType,
    CaseState,
    IntentSource,
    IntentStatus,
    PolicyStage,
)
from dispute_agent.kernel.domain.errors import KillSwitchEngaged, NotFound, PolicyRefused
from dispute_agent.kernel.domain.external import IssueRef
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.kernel.domain.ids import ActionId, CaseId, CorrelationId, IntentId
from dispute_agent.kernel.domain.models import Case
from dispute_agent.kernel.executor.executor import Outcome
from dispute_agent.kernel.policy.verdicts import PolicyContext

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from sqlalchemy import Engine

    from dispute_agent.kernel.domain.actions import Approval
    from dispute_agent.kernel.executor.executor import ActionExecutor
    from dispute_agent.kernel.policy.config import PolicyConfig
    from dispute_agent.kernel.ports import IdGenerator
    from dispute_agent.modules.cross_case.sql_context.repository import (
        PatternRepository,
        StoredPattern,
    )

LABEL = "cross_case_investigation"

#: Action fields the executor may set that the Action model carries.
_MODEL_FIELDS = {
    "sent_at",
    "verified_at",
    "external_ref",
    "last_error_code",
    "verify_attempts",
    "verify_deadline_at",
}


class PatternActionStore:
    """Implements the executor's ActionStore over `pattern_actions`. Issues only."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def insert(self, action: Action) -> Action:
        if action.action_type is not ActionType.UPSERT_ISSUE:
            raise PolicyRefused("DENY", "cross_case_issues_only")
        with self._engine.begin() as conn:
            conn.execute(
                insert(pattern_actions).values(
                    id=str(action.id),
                    pattern_id=str(action.case_id),
                    idempotency_key=str(action.idempotency_key),
                    action_type=action.action_type.value,
                    status=action.status.value,
                    action_json=action.model_dump_json(),
                    extra_json="{}",
                    updated_at=to_iso(action.planned_at),
                )
            )
        return action

    def get(self, action_id: ActionId) -> Action:
        with self._engine.connect() as conn:
            row = conn.execute(
                select(pattern_actions.c.action_json).where(pattern_actions.c.id == str(action_id))
            ).first()
        if row is None:
            raise NotFound(f"no pattern action {action_id}")
        return Action.model_validate_json(row[0])

    def claim(self, action_id: ActionId, worker: str, now: datetime, lease_s: int) -> bool:
        action = self.get(action_id)
        with self._engine.begin() as conn:
            result = conn.execute(
                update(pattern_actions)
                .where(pattern_actions.c.id == str(action_id))
                .where(pattern_actions.c.status == "PLANNED")
                .where(
                    or_(
                        pattern_actions.c.claimed_by.is_(None),
                        pattern_actions.c.claim_expires_at < to_iso(now),
                    )
                )
                .values(
                    claimed_by=worker,
                    claim_expires_at=to_iso(now + timedelta(seconds=lease_s)),
                    action_json=action.model_copy(
                        update={"attempts": action.attempts + 1}
                    ).model_dump_json(),
                    updated_at=to_iso(now),
                )
            )
        return result.rowcount == 1

    def set_status(
        self, action_id: ActionId, status: ActionStatus, now: datetime, **fields: object
    ) -> Action:
        action = self.get(action_id)
        model = {k: v for k, v in fields.items() if k in _MODEL_FIELDS}
        if status is ActionStatus.VERIFIED:
            model.setdefault("verified_at", now)
        if status is ActionStatus.SENT:
            model.setdefault("sent_at", now)
        updated = action.model_copy(update={"status": status, **model})
        Action.model_validate(updated.model_dump())  # the copy skipped validation; do it now
        extra = {k: str(v) for k, v in fields.items() if k not in _MODEL_FIELDS}
        done = status is not ActionStatus.PLANNED
        with self._engine.begin() as conn:
            conn.execute(
                update(pattern_actions)
                .where(pattern_actions.c.id == str(action_id))
                .values(
                    status=status.value,
                    action_json=updated.model_dump_json(),
                    extra_json=json.dumps(extra, sort_keys=True),
                    claimed_by=None if done else pattern_actions.c.claimed_by,
                    claim_expires_at=None if done else pattern_actions.c.claim_expires_at,
                    updated_at=to_iso(now),
                )
            )
        return updated

    def find_approval_for(self, intent_id: object) -> Approval | None:
        return None  # an investigation is never approved: it is ALLOWed or it is refused

    def live_credits(self, shipment_ref: str, reason_code: str) -> int:
        return 0  # no credit can exist here: the table refuses every type but UPSERT_ISSUE

    def has_verified_credit(self, case_id: object) -> bool:
        return False

    def outbound_message_count(self, case_id: object, since: datetime | None = None) -> int:
        return 0

    def for_pattern(self, pattern_id: str) -> list[Action]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                select(pattern_actions.c.action_json)
                .where(pattern_actions.c.pattern_id == pattern_id)
                .order_by(pattern_actions.c.updated_at, pattern_actions.c.id)
            )
            return [Action.model_validate_json(r[0]) for r in rows]


@dataclass(frozen=True)
class InvestigationResult:
    pattern_id: str
    status: str
    reason: str
    external_ref: str | None = None


@dataclass
class Investigations:
    """Open or update a pattern's parent Jira investigation - never directly (I2)."""

    engine: Engine
    repository: PatternRepository
    executor: ActionExecutor
    actions: PatternActionStore
    #: The issue write port for a subject (app.py: IssueWritePort over the ticketing system).
    port: Callable[[CaseId], Any]  # a WritePort (IssueWritePort in app.py)
    ids: IdGenerator
    policy: PolicyConfig
    modules: Mapping[str, bool] = field(default_factory=dict)

    def open(self, pattern_id: str, now: datetime) -> InvestigationResult:
        pattern = self.repository.get(pattern_id)
        record = self._record(pattern_id)
        if record is not None and record["status"] == "VERIFIED":
            return InvestigationResult(
                pattern_id, "VERIFIED", "already_open", record["external_ref"]
            )
        if pattern.status == "DISMISSED":
            return InvestigationResult(pattern_id, "SKIPPED", "pattern_dismissed")

        intent = (
            ActionIntent.model_validate_json(record["intent_json"])
            if record is not None
            else self._propose(pattern, now)
        )
        subject = self._subject(pattern, now)
        if subject is None:
            return self._set(pattern_id, "REFUSED", now, reason="no_member_case")
        port = self.port(subject.id)

        action = self._live_action(pattern_id)
        if action is None:
            try:
                ctx = PolicyContext(
                    case=subject,
                    stage=PolicyStage.EXECUTION,
                    intent=intent,
                    modules=self.modules,
                    policy=self.policy,
                    now=now,
                )
                action, _ = self.executor.plan(
                    subject, intent, ctx, now, ActionId(self.ids.new_id("act"))
                )
            except (PolicyRefused, KillSwitchEngaged) as refused:
                return self._set(pattern_id, "REFUSED", now, reason=str(refused)[:200])
            self._set(pattern_id, "PLANNED", now, action_id=str(action.id))

        if action.status is ActionStatus.PLANNED:
            result = self.executor.execute(action.id, port, intent.payload, now)
            action = self.actions.get(action.id)
            if result.outcome is not Outcome.SENT:
                return self._set(
                    pattern_id,
                    action.status.value,
                    now,
                    reason=result.reason,
                    action_id=str(action.id),
                )

        if action.status is ActionStatus.SENT:
            ref = action.external_ref or ""
            self.executor.verify(action.id, port, IssueRef(key=ref, id=ref), intent.payload, now)
            action = self.actions.get(action.id)

        return self._set(
            pattern_id,
            action.status.value,
            now,
            action_id=str(action.id),
            external_ref=action.external_ref,
            reason=action.last_error_code or action.status.value.lower(),
        )

    # -- helpers ---------------------------------------------------------------------------

    def _propose(self, pattern: StoredPattern, now: datetime) -> ActionIntent:
        members = self.repository.linked(pattern.id)
        payload = IssuePayload(
            summary=f"Cross-case investigation: {pattern.dimension} {pattern.match_value}"[:200],
            description=_description(pattern, members),
            labels=(LABEL, f"pattern-{pattern.id}"),
        )
        intent = ActionIntent(
            id=IntentId(self.ids.new_id("int")),
            case_id=CaseId(pattern.id),
            source_kind=IntentSource.PROPOSAL,
            source_ref=pattern.id,
            action_type=ActionType.UPSERT_ISSUE,
            payload=payload,
            payload_hash=payload_hash(payload),
            status=IntentStatus.PLANNED,
            created_at=now,
        )
        with self.engine.begin() as conn:
            conn.execute(
                insert(pattern_investigations).values(
                    pattern_id=pattern.id,
                    intent_json=intent.model_dump_json(),
                    status="PROPOSED",
                    opened_at=to_iso(now),
                    updated_at=to_iso(now),
                )
            )
        return intent

    def _subject(self, pattern: StoredPattern, now: datetime) -> Case | None:
        """The policy subject: never stored, never a real case's row (module docstring)."""
        members = self.repository.linked(pattern.id)
        if not members:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(
                select(cases.c.facts_json, cases.c.opened_at)
                .where(cases.c.id.in_(members))
                .order_by(cases.c.opened_at.desc())
                .limit(1)
            ).first()
            seq = conn.execute(
                select(func.count())
                .select_from(pattern_actions)
                .where(pattern_actions.c.pattern_id == pattern.id)
            ).scalar_one()
        if row is None:
            return None
        opened = from_iso(row[1])
        return Case(
            id=CaseId(pattern.id),
            pack="weight_dispute",
            facts=CaseFacts.model_validate_json(row[0]),
            state=CaseState.INVESTIGATING,
            version=1,
            correlation_id=CorrelationId(f"corr_{pattern.id.removeprefix('pat_')}"),
            opened_at=opened,
            updated_at=now,
            state_entered_at=now,
            action_seq=int(seq),
        )

    def _live_action(self, pattern_id: str) -> Action | None:
        """The last action, unless it FAILED - a known failure, safe to plan again."""
        found = self.actions.for_pattern(pattern_id)
        if not found or found[-1].status is ActionStatus.FAILED:
            return None
        return found[-1]

    def _record(self, pattern_id: str) -> Any:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(pattern_investigations).where(
                    pattern_investigations.c.pattern_id == pattern_id
                )
            ).first()
        return None if row is None else row._mapping

    def _set(
        self,
        pattern_id: str,
        status: str,
        now: datetime,
        *,
        reason: str | None = None,
        action_id: str | None = None,
        external_ref: str | None = None,
    ) -> InvestigationResult:
        values: dict[str, object] = {
            "status": status,
            "status_reason": reason,
            "updated_at": to_iso(now),
        }
        if action_id is not None:
            values["action_id"] = action_id
        if external_ref is not None:
            values["external_ref"] = external_ref
        with self.engine.begin() as conn:
            conn.execute(
                update(pattern_investigations)
                .where(pattern_investigations.c.pattern_id == pattern_id)
                .values(**values)
            )
        return InvestigationResult(pattern_id, status, reason or "", external_ref)

    def issue_key(self, pattern_id: str) -> str | None:
        record = self._record(pattern_id)
        if record is None or record["status"] != "VERIFIED":
            return None
        return str(record["external_ref"])


def _description(pattern: StoredPattern, members: list[str]) -> str:
    thresholds = pattern.thresholds
    lines = [
        f"Cross-case pattern {pattern.id}: {pattern.summary}",
        "",
        f"Grouped on {pattern.dimension} {pattern.match_value}: at least "
        f"{thresholds.get('min_related_cases')} disputes within {thresholds.get('window_days')} days.",
        "Dispute cases at detection: " + ", ".join(members[:50]),
        "",
        "This is a hypothesis for operations to check, not a finding. Nothing has been decided "
        "because of it: no case is credited, upheld or penalised on the strength of this pattern.",
        "Please record a structured finding: no_fault_found, or inspected with cause_confirmed "
        "or fix_deployed.",
    ]
    return "\n".join(lines)[:4000]
