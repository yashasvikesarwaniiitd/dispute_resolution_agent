"""ProposalStore on SQLite: the audit trail of every decision proposed (P5 carry-over, I8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from dispute_agent.infra.sqlite.mapping import to_iso
from dispute_agent.infra.sqlite.tables import proposals
from dispute_agent.kernel.domain.reasoning import Proposal

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Connection

    from dispute_agent.kernel.domain.ids import CaseId


class SqliteProposalStore:
    """Implements ProposalStore. Used only inside a unit of work."""

    def __init__(self, connection: Connection) -> None:
        self._conn = connection

    def add(self, proposal: Proposal) -> None:
        self._conn.execute(
            proposals.insert().values(
                id=str(proposal.id),
                case_id=str(proposal.case_id),
                gather_round=max(proposal.gather_round, 1),
                action=proposal.action.value,
                confidence=proposal.confidence.value,
                reasoner=proposal.reasoner,
                fallback_used=int(proposal.fallback_used),
                proposal_json=proposal.model_dump_json(),
                prompt_name=proposal.prompt_name,
                prompt_version=proposal.prompt_version,
                prompt_hash=None if proposal.prompt_hash is None else str(proposal.prompt_hash),
                model_id=proposal.model_id,
                model_route=proposal.model_route,
                briefing_hash=str(proposal.briefing_hash),
                created_at=to_iso(proposal.created_at),
            )
        )

    def for_case(self, case_id: CaseId) -> Sequence[Proposal]:
        rows = self._conn.execute(
            select(proposals.c.proposal_json)
            .where(proposals.c.case_id == str(case_id))
            .order_by(proposals.c.created_at, proposals.c.id)
        ).all()
        return [Proposal.model_validate_json(row[0]) for row in rows]


__all__ = ["SqliteProposalStore"]
