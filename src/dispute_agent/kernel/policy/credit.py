"""The credit amount, derived by code and nothing else (money-rules.md section 3, I3, I9).

The amount a seller is credited is never taken from a model, a reply or a proposal. It is a
pure function of the stored assessment and of what has already been credited on the same
charge line, so a second dispute on the same overcharge can never pay twice:

    calculation_error / remeasurement_lower:  charged - lowest proven correct - already credited
    duplicate_charge:                         duplicate debits - already credited for duplicates

Only corrected charges whose reason is in `definite_errors` count as proven. A result of zero
or less means "no credit", and is returned as None so no caller can post it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.values import Money

if TYPE_CHECKING:
    from dispute_agent.kernel.domain.evidence import ChargeAssessment

#: The three code-proven reasons (D29). Anything else is not a credit reason.
PROVEN_REASONS = ("calculation_error", "remeasurement_lower", "duplicate_charge")


def credit_amount(
    assessment: ChargeAssessment,
    reason: str,
    already_credited: Money,
    already_credited_duplicate: Money,
) -> Money | None:
    if reason not in assessment.definite_errors:
        return None
    if reason in ("calculation_error", "remeasurement_lower"):
        correct = lowest_proven_correct_charge(assessment)
        if assessment.charged is None or correct is None:
            return None
        owed = assessment.charged.to_paise() - correct.to_paise() - already_credited.to_paise()
    elif reason == "duplicate_charge":
        if assessment.duplicate_debit_total is None:
            return None
        owed = assessment.duplicate_debit_total.to_paise() - already_credited_duplicate.to_paise()
    else:
        return None
    return Money.from_paise(owed) if owed > 0 else None


def lowest_proven_correct_charge(assessment: ChargeAssessment) -> Money | None:
    proven: list[Money] = []
    if "calculation_error" in assessment.definite_errors and assessment.recalculated:
        proven.append(assessment.recalculated)
    if "remeasurement_lower" in assessment.definite_errors and assessment.recalculated_remeasured:
        proven.append(assessment.recalculated_remeasured)
    return min(proven, key=lambda m: m.to_paise()) if proven else None


__all__ = ["PROVEN_REASONS", "credit_amount", "lowest_proven_correct_charge"]
