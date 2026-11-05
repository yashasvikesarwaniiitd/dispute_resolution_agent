"""The Case Explainer's only window onto the data: read-only, enforced by SQLite (ADR 0039).

The database is opened with the URI `mode=ro`, so SQLite itself refuses any write on this
connection - the guarantee does not rest on the code below being careful. Every method is a
SELECT, every result is a plain dict, and text written by an outside party (the seller's
statement) is neutralised and handed over under a key starting `untrusted_` (I5).

What is deliberately left out: the seller's email address (the explainer never needs it), raw
evidence payloads, and prompt or response content.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from dispute_agent.modules.reasoning.templates import neutralise

if TYPE_CHECKING:
    from pathlib import Path

MAX_EVENTS = 40
MAX_STATEMENT_CHARS = 500


class CaseNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class CaseReader:
    """Read-only access to one case database file."""

    database: Path

    def _connect(self) -> sqlite3.Connection:
        uri = f"{self.database.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self, sql: str, *params: object) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            return [dict(row) for row in conn.execute(sql, params)]

    # -- the four views the tools expose ------------------------------------------------------

    def overview(self, case_id: str) -> dict[str, Any]:
        rows = self._rows(
            "SELECT id, pack, shipment_ref, claim_ref, seller_ref, facts_json, state, opened_at,"
            " updated_at, closed_at, review_code, review_module, review_detail, gather_round,"
            " reopen_count FROM cases WHERE id = ?",
            case_id,
        )
        if not rows:
            raise CaseNotFoundError(case_id)
        case = rows[0]
        facts = json.loads(case.pop("facts_json") or "{}")
        statement = (facts.get("statement") or {}).get("text") if isinstance(facts, dict) else None
        assessment = self._rows(
            "SELECT eligible, charged_paise, recalculated_paise, discrepancy_paise, created_at"
            " FROM assessments WHERE case_id = ? ORDER BY created_at DESC LIMIT 1",
            case_id,
        )
        return {
            **case,
            "disputed_amount": facts.get("disputed_amount") if isinstance(facts, dict) else None,
            "latest_assessment": assessment[0] if assessment else None,
            "untrusted_seller_statement": (
                neutralise(str(statement)[:MAX_STATEMENT_CHARS]) if statement else None
            ),
        }

    def timeline(self, case_id: str) -> list[dict[str, Any]]:
        self.overview(case_id)  # raises CaseNotFoundError with a clear message
        rows = self._rows(
            "SELECT seq, type, occurred_at, actor_kind, actor_id, cause_kind, cause_ref,"
            " from_state, to_state FROM events WHERE case_id = ? ORDER BY seq DESC LIMIT ?",
            case_id,
            MAX_EVENTS,
        )
        return list(reversed(rows))

    def decisions(self, case_id: str) -> dict[str, Any]:
        self.overview(case_id)
        return {
            "proposals": self._rows(
                "SELECT created_at, gather_round, action, confidence, reasoner, fallback_used,"
                " prompt_name, prompt_version, model_route FROM proposals WHERE case_id = ?"
                " ORDER BY created_at",
                case_id,
            ),
            "approvals": self._rows(
                "SELECT requested_at, subject, amount_paise, message_kind, decision, decided_by,"
                " decided_at, expires_at FROM approvals WHERE case_id = ? ORDER BY requested_at",
                case_id,
            ),
            "actions": self._rows(
                "SELECT planned_at, action_type, status, external_ref, last_error_code,"
                " sent_at, verified_at FROM actions WHERE case_id = ? ORDER BY action_seq",
                case_id,
            ),
        }

    def recent_case_ids(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT id, state, review_code, updated_at FROM cases ORDER BY updated_at DESC LIMIT ?",
            limit,
        )


__all__ = ["CaseNotFoundError", "CaseReader"]
