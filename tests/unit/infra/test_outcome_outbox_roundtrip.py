"""Bug fix (Phase 7): a stored outcome must read back. Before, the first closed case made
every runner tick raise, because strict validation refused the stored enum and datetime."""

from __future__ import annotations

from dispute_agent.infra.sqlite.outcome_outbox import SqliteOutcomeOutbox
from dispute_agent.kernel.domain.enums import CaseState
from dispute_agent.kernel.domain.ids import CaseId, OutcomeId
from dispute_agent.kernel.domain.patterns import CaseOutcome
from tests.support.patterns import T0, add_case, facts, new_engine


def test_an_outcome_written_to_the_outbox_reads_back_unchanged() -> None:
    engine = new_engine()
    add_case(engine, "case_1")
    outcome = CaseOutcome(
        id=OutcomeId("out_1"),
        case_id=CaseId("case_1"),
        state=CaseState.REJECTED_INELIGIBLE,
        resolved=False,
        facts=facts(),
        closed_at=T0,
    )
    with engine.begin() as conn:
        SqliteOutcomeOutbox(conn).add(outcome, T0)
    with engine.connect() as conn:
        assert SqliteOutcomeOutbox(conn).undelivered() == [outcome]
