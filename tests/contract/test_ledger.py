"""Contract: Ledger (kernel-interfaces.md 6.4, money-rules.md).

Simulated throughout (I11), which is exactly why the suite has to be strict: the simulated
ledger is the only ledger this project will ever have, so every guarantee the executor claims
about money is a guarantee about *this* code. A fake that always posts cleanly would make the
credit path look safe without testing any of it.

The reason `get_entries` matters as much as `post_credit`: R16 caps a credit at the
outstanding charge, computed from these entries, and money-rules.md says an unreadable ledger
refuses the credit rather than guessing (`ledger_state_unknown`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from tests.contract.conftest import build_or_skip, params, pending

if TYPE_CHECKING:
    from dispute_agent.kernel.ports import Ledger

ADAPTERS = [
    pending("simulated", phase="Phase 5 (card P5-02)"),
]


@pytest.fixture(params=params(ADAPTERS))
def ledger(request: pytest.FixtureRequest) -> Ledger:
    return cast("Ledger", build_or_skip(request.param))


class TestPosting:
    def test_a_credit_returns_a_transaction_reference(self, ledger: Ledger) -> None: ...

    def test_the_same_key_twice_posts_once(self, ledger: Ledger) -> None:
        """I3 and the duplicate-side-effect gate, which must be zero."""

    def test_the_second_post_reports_itself_as_a_duplicate(self, ledger: Ledger) -> None: ...

    def test_amounts_are_exact(self, ledger: Ledger) -> None:
        """I9: a rupee lost to binary rounding is a rupee a seller will write in about."""


class TestLookupAndReadBack:
    def test_find_transaction_returns_the_earlier_reference(self, ledger: Ledger) -> None: ...

    def test_get_transaction_returns_the_amount_that_was_posted(self, ledger: Ledger) -> None:
        """I2: verified by reading back, never by believing the receipt."""

    def test_a_failed_post_leaves_no_entry(self, ledger: Ledger) -> None: ...


class TestEntries:
    def test_get_entries_returns_debits_and_credits_for_the_shipment(self, ledger: Ledger) -> None:
        """R16 computes the outstanding charge from these."""

    def test_an_unreadable_ledger_raises_rather_than_returning_nothing(
        self, ledger: Ledger
    ) -> None:
        """Empty would read as "nothing has been charged", and the cap would mean nothing."""
