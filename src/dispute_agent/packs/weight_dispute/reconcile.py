"""Definite errors: the only basis for a credit (money-rules.md 2-4, D29, R14, I3, I9).

This module answers one question — *has code proved, from evidence, that this seller was
overcharged?* — and it answers it in exactly three ways, because money-rules.md defines
exactly three provable reasons:

- `calculation_error`: recalculating from the **same inputs the bill used** gives less than
  was charged. Wrong slab, wrong zone, wrong divisor, keyed weight.
- `remeasurement_lower`: a **structured** hub finding gives a lower chargeable weight, and
  recalculating with it gives less. The seller's photos never prove this; the hub's
  measurement does.
- `duplicate_charge`: the ledger holds two or more DEBITs for the same shipment and charge
  line.

**Free text never contributes.** Not a seller's statement, not an AI's reading of a reply,
not a note on an ops finding. A seller writing "I was charged twice" is a reason to look at
the ledger; the ledger is what proves it (agent-design.md section 6, I7). `prove` takes
structured values only — there is no parameter it could read a sentence from.

**Two errors mean no credit.** R14: when more than one definite error is proven, a person
decides. Two proofs of overcharge are two different numbers, and netting them automatically
is how a seller gets paid twice for one mistake.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.values import Money
from dispute_agent.packs.weight_dispute.calculator import Dimensions, calculate, difference

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dispute_agent.packs.weight_dispute.rate_card import RateCard

CALCULATION_ERROR = "calculation_error"
REMEASUREMENT_LOWER = "remeasurement_lower"
DUPLICATE_CHARGE = "duplicate_charge"

#: The whole vocabulary. Adding one needs a pack rule that proves it and an ADR (money-rules
#: section 2), which is why this is a frozenset and not an open string.
REASON_CODES = frozenset({CALCULATION_ERROR, REMEASUREMENT_LOWER, DUPLICATE_CHARGE})


@dataclass(frozen=True)
class LedgerLine:
    """One ledger entry, as the reconciliation reads it."""

    transaction_id: str
    kind: str
    amount_paise: int
    claim_ref: str
    reason_code: str | None = None


@dataclass(frozen=True)
class Reconciliation:
    """What was proven, and the corrected charges that prove it.

    `definite_errors` is the output everything downstream depends on: R14 reads its length,
    R15 reads the amount derived from it, and a proposal naming a reason not in here is
    refused. Keeping the corrected charges alongside means the number can be explained.
    """

    charged: Money
    recalculated: Money | None = None
    recalculated_remeasured: Money | None = None
    duplicate_debit_total: Money = field(default_factory=lambda: Money.of("0.00"))
    definite_errors: tuple[str, ...] = ()

    @property
    def single_error(self) -> str | None:
        """The one proven reason, or None when there are none or more than one (R14)."""
        return self.definite_errors[0] if len(self.definite_errors) == 1 else None

    def lowest_proven_correct_charge(self) -> Money | None:
        """The least of the corrected charges whose reason was actually proven.

        Only proven ones: a recalculation that happens to be lower but whose reason is not
        in `definite_errors` has not been shown to be the right number, and using it would
        be paying out on a hunch.
        """
        candidates: list[Money] = []
        if CALCULATION_ERROR in self.definite_errors and self.recalculated is not None:
            candidates.append(self.recalculated)
        if (
            REMEASUREMENT_LOWER in self.definite_errors
            and self.recalculated_remeasured is not None
        ):
            candidates.append(self.recalculated_remeasured)
        return min(candidates, default=None)


def prove(
    *,
    charged: Money,
    billed_dimensions: Dimensions,
    billed_dead_grams: int,
    zone: str,
    card: RateCard,
    ledger: Sequence[LedgerLine],
    claim_ref: str,
    packaging: str | None = None,
    remeasured: Dimensions | None = None,
    remeasured_dead_grams: int | None = None,
) -> Reconciliation:
    """Prove what can be proved, from structured values only.

    Every parameter is a number, a dimension or a ledger row. There is deliberately no
    parameter a sentence could arrive through: a seller's claim, an AI's reading and a note
    on a finding all have no way into this function, which is I7 and D29 made structural
    rather than remembered.
    """
    errors: list[str] = []

    # --- calculation_error: recalculate from the inputs the bill used --------------------
    recalculated = calculate(billed_dimensions, billed_dead_grams, zone, card, packaging).amount
    if recalculated < charged:
        errors.append(CALCULATION_ERROR)

    # --- remeasurement_lower: only a structured hub finding counts ------------------------
    recalculated_remeasured: Money | None = None
    if remeasured is not None and remeasured_dead_grams is not None:
        recalculated_remeasured = calculate(
            remeasured, remeasured_dead_grams, zone, card, packaging
        ).amount
        if recalculated_remeasured < charged:
            errors.append(REMEASUREMENT_LOWER)

    # --- duplicate_charge: two or more DEBITs on one charge line --------------------------
    duplicate_total = _duplicate_debit_total(ledger, claim_ref)
    if duplicate_total > Money.of("0.00"):
        errors.append(DUPLICATE_CHARGE)

    return Reconciliation(
        charged=charged,
        recalculated=recalculated,
        recalculated_remeasured=recalculated_remeasured,
        duplicate_debit_total=duplicate_total,
        definite_errors=tuple(errors),
    )


def credit_amount(
    reconciliation: Reconciliation,
    reason: str,
    already_credited: Money | None = None,
    already_credited_duplicate: Money | None = None,
) -> Money:
    """What is still owed (money-rules.md section 3).

    Netting against earlier credits is what stops a second dispute paying twice for one
    overcharge: if case A credited 200 on a 1,000 charge and case B later proves the true
    charge was 600, case B owes 1,000 - 600 - 200 = 200, and the total credited is the real
    400.
    """
    credited = already_credited or Money.of("0.00")
    credited_duplicate = already_credited_duplicate or Money.of("0.00")

    if reason not in REASON_CODES:
        raise _unproven(reason)
    if reason not in reconciliation.definite_errors:
        # The reason has to be one this reconciliation actually proved. R14 refuses the rest.
        raise _unproven(reason)

    if reason == DUPLICATE_CHARGE:
        return difference(reconciliation.duplicate_debit_total, credited_duplicate)

    correct = reconciliation.lowest_proven_correct_charge()
    if correct is None:
        raise _unproven(reason)
    return difference(difference(reconciliation.charged, correct), credited)


def _duplicate_debit_total(ledger: Sequence[LedgerLine], claim_ref: str) -> Money:
    """Everything beyond the first DEBIT on this charge line.

    The *first* debit is the charge; anything identical after it is the duplicate, so the
    amount owed is the sum of the extras rather than the whole lot.
    """
    debits = [
        line for line in ledger if line.kind == "DEBIT" and line.claim_ref == claim_ref
    ]
    if len(debits) <= 1:
        return Money.of("0.00")
    extra_paise = sum(line.amount_paise for line in debits[1:])
    return Money.from_paise(extra_paise)


def _unproven(reason: str) -> Exception:
    from dispute_agent.kernel.domain.errors import DomainValidationError  # noqa: PLC0415

    return DomainValidationError(
        f"{reason!r} is not a proven definite error for this case; a credit needs code to "
        "have proved the reason it names (D29, R14)"
    )
