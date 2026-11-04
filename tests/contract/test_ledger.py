"""Contract: Ledger (kernel-interfaces.md 6.4, money-rules.md sections 7 and 8).

Simulated throughout (I11), which is exactly why the suite has to be strict: the simulated
ledger is the only ledger this project will ever have, so every guarantee the executor claims
about money is a guarantee about *this* code. A fake that always posts cleanly would make the
credit path look safe without testing any of it.

The reason `get_entries` matters as much as `post_credit`: R16 caps a credit at the
outstanding charge, computed from these entries, and money-rules.md says an unreadable ledger
refuses the credit rather than guessing (`ledger_state_unknown`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from dispute_agent.kernel.domain.errors import (
    AmbiguousOutcome,
    ExternalNotFound,
    ModuleUnavailable,
    PermanentPortError,
    TransientPortError,
)
from dispute_agent.kernel.domain.external import CreditRequest
from dispute_agent.kernel.domain.ids import CaseId, IdempotencyKey
from dispute_agent.kernel.domain.values import Money
from dispute_agent.modules.ledger.simulated.ledger import SimulatedLedger
from tests.contract.conftest import build_or_skip, params, ready

if TYPE_CHECKING:
    from pathlib import Path

SHIPMENT = "SHP-000001"
KEY = IdempotencyKey("wd1:" + "a" * 64)
OTHER_KEY = IdempotencyKey("wd1:" + "b" * 64)


def build_simulated(tmp: Path, **kwargs: Any) -> SimulatedLedger:
    ledger = SimulatedLedger(tmp / "ledger.sqlite3", **kwargs)
    ledger.seed_debit(SHIPMENT, Money.of("2000.00"), "weight_charge", "txn_debit_1")
    return ledger


ADAPTERS = [ready("simulated", lambda: build_simulated)]


@pytest.fixture(params=params(ADAPTERS))
def make(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    builder = build_or_skip(request.param)
    return lambda **kwargs: builder(tmp_path, **kwargs)


@pytest.fixture
def ledger(make: Any) -> Any:
    return make()


def credit(amount: str = "1820.00", reason: str = "calculation_error") -> CreditRequest:
    return CreditRequest(
        shipment_ref=SHIPMENT,
        amount=Money.of(amount),
        reason_code=reason,
        case_id=CaseId("case_01HQ"),
    )


class TestPosting:
    def test_a_credit_returns_a_transaction_reference(self, ledger: Any) -> None:
        ref = ledger.post_credit(credit(), KEY)
        assert ref.transaction_id.startswith("txn_")
        assert ref.duplicate is False

    def test_the_same_key_twice_posts_once(self, ledger: Any) -> None:
        """I3 and the duplicate-side-effect gate, which must be zero."""
        ledger.post_credit(credit(), KEY)
        ledger.post_credit(credit(), KEY)
        assert ledger.credit_count(SHIPMENT) == 1

    def test_the_second_post_reports_itself_as_a_duplicate(self, ledger: Any) -> None:
        first = ledger.post_credit(credit(), KEY)
        second = ledger.post_credit(credit(), KEY)
        assert second.duplicate is True
        assert second.transaction_id == first.transaction_id

    def test_amounts_are_exact(self, ledger: Any) -> None:
        """I9: a rupee lost to binary rounding is a rupee a seller will write in about."""
        ref = ledger.post_credit(credit("0.01"), KEY)
        assert ledger.get_transaction(ref).amount == Money.of("0.01")

    def test_a_second_key_for_the_same_shipment_and_reason_is_refused(self, ledger: Any) -> None:
        ledger.post_credit(credit("100.00"), KEY)
        with pytest.raises(PermanentPortError) as raised:
            ledger.post_credit(credit("100.00"), OTHER_KEY)
        assert raised.value.status == 409

    def test_more_than_is_outstanding_is_refused(self, ledger: Any) -> None:
        with pytest.raises(PermanentPortError):
            ledger.post_credit(credit("2000.01"), KEY)

    def test_an_unknown_reason_is_refused(self, ledger: Any) -> None:
        with pytest.raises(PermanentPortError):
            ledger.post_credit(credit(reason="goodwill"), KEY)


class TestLookupAndReadBack:
    def test_find_transaction_returns_the_earlier_reference(self, ledger: Any) -> None:
        ref = ledger.post_credit(credit(), KEY)
        assert ledger.find_transaction(KEY).transaction_id == ref.transaction_id
        assert ledger.find_transaction(OTHER_KEY) is None

    def test_get_transaction_returns_the_amount_that_was_posted(self, ledger: Any) -> None:
        """I2: verified by reading back, never by believing the receipt."""
        snapshot = ledger.get_transaction(ledger.post_credit(credit(), KEY))
        assert (snapshot.amount, snapshot.shipment_ref, snapshot.reason_code) == (
            Money.of("1820.00"),
            SHIPMENT,
            "calculation_error",
        )
        assert (snapshot.type, snapshot.status, snapshot.idempotency_key) == (
            "CREDIT",
            "POSTED",
            KEY,
        )

    def test_a_failed_post_leaves_no_entry(self, make: Any) -> None:
        ledger = make(failure_mode="reject_400")
        with pytest.raises(PermanentPortError):
            ledger.post_credit(credit(), KEY)
        assert ledger.credit_count() == 0


class TestEntries:
    def test_get_entries_returns_debits_and_credits_for_the_shipment(self, ledger: Any) -> None:
        """R16 computes the outstanding charge from these."""
        ledger.post_credit(credit(), KEY)
        kinds = [e.type for e in ledger.get_entries(SHIPMENT)]
        assert kinds == ["DEBIT", "CREDIT"]

    def test_an_unreadable_ledger_raises_rather_than_returning_nothing(self, make: Any) -> None:
        """Empty would read as "nothing has been charged", and the cap would mean nothing."""
        with pytest.raises(ModuleUnavailable):
            make(failure_mode="unreadable").get_entries(SHIPMENT)


class TestFailureModes:
    """money-rules.md section 7, row by row: what the ledger does, so the executor can be
    held to what it must end in."""

    def test_timeout_before_writes_nothing(self, make: Any) -> None:
        ledger = make(failure_mode="timeout_before")
        with pytest.raises(TransientPortError):
            ledger.post_credit(credit(), KEY)
        assert ledger.credit_count() == 0

    @pytest.mark.parametrize("mode", ["timeout_after", "error_500_applied"])
    def test_ambiguous_after_writing_is_found_by_lookup(self, make: Any, mode: str) -> None:
        ledger = make(failure_mode=mode)
        with pytest.raises(AmbiguousOutcome):
            ledger.post_credit(credit(), KEY)
        assert ledger.find_transaction(KEY) is not None

    def test_ambiguous_without_writing_is_safe_to_retry(self, make: Any) -> None:
        ledger = make(failure_mode="error_500_not_applied")
        with pytest.raises(AmbiguousOutcome):
            ledger.post_credit(credit(), KEY)
        assert ledger.find_transaction(KEY) is None

    def test_delayed_visibility_hides_then_shows(self, make: Any) -> None:
        now = [datetime(2026, 11, 4, tzinfo=UTC)]
        ledger = make(failure_mode="delayed_visibility", clock=lambda: now[0])
        ref = ledger.post_credit(credit(), KEY)
        with pytest.raises(ExternalNotFound):
            ledger.get_transaction(ref)
        now[0] += timedelta(seconds=31)
        assert ledger.get_transaction(ref).amount == Money.of("1820.00")

    def test_wrong_amount_posts_one_paisa_more(self, make: Any) -> None:
        ledger = make(failure_mode="wrong_amount")
        snapshot = ledger.get_transaction(ledger.post_credit(credit(), KEY))
        assert snapshot.amount == Money.of("1820.01")

    def test_the_ledger_has_its_own_file(self, make: Any, tmp_path: Path) -> None:
        """money-rules.md section 7: a case-database bug cannot fake a ledger entry."""
        make()
        assert (tmp_path / "ledger.sqlite3").is_file()
