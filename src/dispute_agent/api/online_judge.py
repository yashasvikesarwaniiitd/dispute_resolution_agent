"""Online evaluation (ADR 0066): the decision judge on the AI's live decisions, when switched on.

Offline, the judge was calibrated on the owner's labels (25 of 28 agree, every critical flaw
caught). Online there is no answer key: each new decision the AI makes on a live case is shown
to the same judge with the evidence the AI had, and the judge says whether the explanation
holds up. The Evals screen shows where the two disagree.

Three parts, none of which can change a case:

- `OnlineEvals`: a small SQLite file next to the case store - the switch, and one row per
  decision. Its own file, so a slow write here never touches the case store's lock.
- `JudgedReasoner`: wraps the live reasoner. While the switch is on it records each AI proposal
  with the evidence lines the judge will read. Recording is best effort: a failure is logged
  and the proposal goes on exactly as it would have.
- `OnlineJudgeJob`: a runner job. While the switch is on it judges one recorded decision per
  tick, outside any case transaction. Off, it does nothing and spends nothing; decisions
  recorded earlier wait until it is on again.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import structlog

from dispute_agent.kernel.domain.errors import BudgetExceeded

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from dispute_agent.kernel.domain.reasoning import CaseBriefing, Proposal
    from dispute_agent.kernel.ports import Reasoner

log = structlog.get_logger(__name__)

FILE = "online_evals.sqlite3"
#: The judge's own call log, so its spending cap counts only its own calls.
JUDGE_LOG = "online_judge_calls.sqlite3"
#: shortcut: the cap is summed from JUDGE_LOG, which a host restart wipes (audit finding 10);
#: move the running total somewhere durable when online evaluation runs unattended.
CAP_USD = Decimal("0.50")

SCHEMA = """
create table if not exists switch (id integer primary key check (id = 1), is_on integer not null,
    changed_at text not null);
create table if not exists decisions (
    proposal_id text primary key, case_id text not null, round integer not null,
    created_at text not null, action text not null, reason_code text, rationale text not null,
    evidence_json text not null, status text not null default 'pending', verdict text,
    judgement_json text, error text, judged_at text);
"""


@dataclass(frozen=True)
class OnlineEvals:
    path: Path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.executescript(SCHEMA)
        return conn

    def is_on(self) -> bool:
        if not self.path.is_file():
            return False  # off until someone turns it on
        with closing(self._connect()) as c:
            row = c.execute("select is_on from switch where id = 1").fetchone()
        return bool(row and row[0])

    def set_on(self, on: bool) -> None:
        with closing(self._connect()) as c, c:
            c.execute(
                "insert into switch values (1, ?, ?) on conflict(id) do update "
                "set is_on = excluded.is_on, changed_at = excluded.changed_at",
                (int(on), _now()),
            )

    def record(self, proposal: Proposal, evidence: list[str]) -> None:
        reason = proposal.action_parameters.reason_code
        with closing(self._connect()) as c, c:
            c.execute(
                "insert or ignore into decisions (proposal_id, case_id, round, created_at, "
                "action, reason_code, rationale, evidence_json) values (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(proposal.id),
                    str(proposal.case_id),
                    proposal.gather_round,
                    proposal.created_at.isoformat(),
                    proposal.action.value,
                    reason,
                    proposal.rationale,
                    json.dumps(evidence),
                ),
            )

    def next_pending(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        with closing(self._connect()) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "select * from decisions where status = 'pending' order by created_at limit 1"
            ).fetchone()
        return dict(row) if row else None

    def finish(
        self, proposal_id: str, *, judgement: dict[str, Any] | None, error: str = ""
    ) -> None:
        with closing(self._connect()) as c, c:
            c.execute(
                "update decisions set status = ?, verdict = ?, judgement_json = ?, error = ?, "
                "judged_at = ? where proposal_id = ?",
                (
                    "judged" if judgement else "error",
                    judgement["verdict"] if judgement else None,
                    json.dumps(judgement) if judgement else None,
                    error or None,
                    _now(),
                    proposal_id,
                ),
            )

    def rows(self) -> list[dict[str, Any]]:
        """Every recorded decision, newest first, with the judgement parsed."""
        if not self.path.is_file():
            return []
        with closing(self._connect()) as c:
            c.row_factory = sqlite3.Row
            found = c.execute("select * from decisions order by created_at desc").fetchall()
        out = []
        for row in found:
            item = dict(row)
            item["judgement"] = json.loads(item.pop("judgement_json") or "null")
            item["evidence"] = json.loads(item.pop("evidence_json"))
            out.append(item)
        return out


def spent_usd(judge_log: Path) -> Decimal:
    """What the online judge has spent, from its own call log (the gateway writes it)."""
    if not judge_log.is_file():
        return Decimal(0)
    with closing(sqlite3.connect(judge_log, timeout=5)) as c:
        try:
            micros = c.execute(
                "select coalesce(sum(cost_usd_micros), 0) from model_calls"
            ).fetchone()
        except sqlite3.OperationalError:  # no call made yet: the table does not exist
            return Decimal(0)
    return Decimal(int(micros[0])) / Decimal(1_000_000)


@dataclass
class JudgedReasoner:
    """The live reasoner, unchanged, plus a record of each AI decision while the switch is on."""

    inner: Reasoner
    store: OnlineEvals
    lines: Callable[[CaseBriefing], list[str]]

    def propose(self, briefing: CaseBriefing) -> Proposal:
        proposal = self.inner.propose(briefing)
        if proposal.reasoner == "llm":
            try:
                if self.store.is_on():
                    self.store.record(proposal, self.lines(briefing))
            except Exception as failed:  # evaluation must never change what happens to a case
                log.error("online_eval_record_failed", error=type(failed).__name__)
        return proposal


@dataclass
class OnlineJudgeJob:
    """Judges one recorded decision per runner tick while the switch is on."""

    store: OnlineEvals
    #: Builds the judge on first use, so a missing key fails one decision, not the start-up.
    build_judge: Callable[[], Any]
    name: str = "online_judge"
    _judge: Any = field(default=None, init=False)

    def due(self, now: datetime) -> bool:
        return self.store.is_on()

    def run(self, now: datetime) -> None:
        item = self.store.next_pending()
        if item is None:
            return
        try:
            if self._judge is None:
                self._judge = self.build_judge()
            found = self._judge.judge(
                evidence=json.loads(item["evidence_json"]),
                action=item["action"],
                explanation=item["rationale"],
                ref=item["case_id"],
                credit=(
                    f"{item['reason_code']}, amount set by code after this decision"
                    if item["action"] == "propose_credit" and item["reason_code"]
                    else ""
                ),
            )
        except BudgetExceeded:
            # The cap is spent: switch off and leave the decision waiting, rather than mark
            # every remaining one as failed.
            self.store.set_on(False)
            log.warning("online_judge_cap_reached")
            return
        except Exception as failed:  # recorded on the row, shown on the screen; no case moves
            self.store.finish(
                item["proposal_id"],
                judgement=None,
                error=f"{type(failed).__name__}: {str(failed)[:160]}",
            )
            return
        self.store.finish(
            item["proposal_id"],
            judgement={**found.model_dump(mode="json"), "verdict": found.verdict},
        )


def _now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "CAP_USD",
    "FILE",
    "JUDGE_LOG",
    "JudgedReasoner",
    "OnlineEvals",
    "OnlineJudgeJob",
    "spent_usd",
]
