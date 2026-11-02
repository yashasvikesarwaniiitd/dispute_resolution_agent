# Money rules (Stage A2)

Status: draft for owner approval. Owner decisions: D29 (credits only for code-proven reasons), D30 (no amount caps; structural limit only). All rates and amounts are illustrative synthetic configuration (D10).

Plain-language summary: money moves only when code has proved, from evidence, that the seller was overcharged, and only by exactly the amount code calculated. A person approves every credit. One credit per shipment and reason, ever. The simulated ledger behaves like a careful real one: it refuses duplicates and lets us read back what it did.

## 1. Credit eligibility

A credit intent can be created only if all of these hold (checked by policy rules at planning and again at execution):

1. The pack's eligibility for the case is `eligible` (for weight disputes: the shipment exists, was delivered, and the dispute was raised within the pack's dispute window).
2. The assessment proves exactly one definite error, and the proposal's `reason_code` names it (R14). More than one -> a person decides (`multiple_definite_errors`).
3. The amount equals `credit_amount(assessment, reason_code)` exactly (R15) and does not exceed the outstanding charge (R16).
4. No credit for the same shipment and reason is live or done in any case (R17), and this case has no verified credit (R18).
5. A human approves the exact amount (R13; `approvals.md`).

The model's `propose_credit` is a request that starts this check. It is never the reason a credit passes (D29).

## 2. Reason codes

Each reason code is defined by a deterministic condition the pack computes into `ChargeAssessment.definite_errors`. Codes are configuration (`credits.reason_codes`); adding one requires a pack rule that proves it and an ADR.

| Code | Proven when (pack computes) | Evidence it needs |
|------|-----------------------------|-------------------|
| `calculation_error` | Recalculating the charge from the **same inputs the bill used** (billed dimensions, billed dead weight, zone, rate card version in force on the ship date) gives a lower amount than charged. Examples: wrong slab rounding, wrong zone, wrong divisor, arithmetic error. | billed inputs, rate card version, charge line |
| `remeasurement_lower` | A structured operations finding (`OpsFinding.kind = remeasured`, with measured dimensions and/or dead weight) gives a chargeable weight in a lower slab than the bill, and recalculating with the remeasured values gives a lower amount. The seller's own photos or claims never prove this; only the hub remeasurement does. | ops finding (structured fields only), rate card |
| `duplicate_charge` | The ledger shows two or more DEBIT entries for the same shipment and charge line (`claim_ref`). | ledger entries |

A seller's statement, an AI reading of a reply, a historical weight, or a cross-case pattern can never by themselves prove a reason code (I7, D29). They can lead to a remeasurement request, whose structured result can.

## 3. Amount derivation

```python
def credit_amount(a: ChargeAssessment, reason: str, already_credited: Money,
                  already_credited_duplicate: Money) -> Money:
    # already_credited: every CREDIT on this charge line that is not FAILED, in any case (A2 review finding 2)
    match reason:
        case "calculation_error" | "remeasurement_lower":
            correct = lowest_proven_correct_charge(a)      # min of the proven corrected charges
            return a.charged - correct - already_credited  # what is still owed after earlier credits
        case "duplicate_charge":
            return a.duplicate_debit_total - already_credited_duplicate
```

`lowest_proven_correct_charge` uses only the corrected charges (`recalculated`, `recalculated_remeasured`) whose reason is in `definite_errors`. Netting against earlier credits means a second dispute on the same charge can never pay twice for the same overcharge: if case A credited 200 (1000 -> 800) and case B later proves a remeasurement to 600, case B's amount is 1000 - 600 - 200 = 200, so the total credited is 400, the true overcharge.

- Every operand is a `Money` already present in the assessment, each tied to `Fact`s (`fact_ids` on the credit payload), so the approver sees where each number came from.
- The result must be positive. Zero or negative means "no credit" and the rule refuses it.
- A shipment that already has any non-FAILED credit makes a new case escalate (R24), so a person sees every second credit on a charge line even when the arithmetic allows it.
- `ChargeAssessment` gains two optional fields for this (applied to `kernel-interfaces.md` section 13): `recalculated_remeasured: Money | None` and `duplicate_debit_total: Money | None`.

## 4. One credit per case, one per shipment and reason

- Per shipment and reason, across all cases: at most one credit in PLANNED, SENT, UNKNOWN or VERIFIED (R17, `ux_actions_one_credit`). A FAILED credit (proven not applied) frees the slot.
- Per case: at most one verified credit (R18). If the evidence later proves a second, different error, a person handles it (the case is escalated or handed over); the agent does not stack credits.
- An UNKNOWN credit blocks every new credit for the same shipment and reason until a human resolves it (`confirmed_done` -> VERIFIED, `confirmed_not_done` -> FAILED).

## 5. Limits

- No configured amount caps (D30). A person approves every credit, and the amount is exactly what code computed.
- Structural limit (R16): `amount <= outstanding_charge`, where `outstanding_charge` = sum of DEBITs for the shipment's charge line minus every CREDIT on it that is not FAILED. If the ledger cannot be read, the credit is refused (`ledger_state_unknown`).
- Caps can be added later as configuration plus a rule (an ADR), without changing the kernel.

## 6. Rounding

- The kernel never rounds (ADR 0011). `Money` refuses more than two decimal places.
- Rates in the rate card are whole paise per slab. A charge is `rate_per_slab * slab_count`: an integer number of paise, so no rounding exists in the normal path.
- Weight is rounded **up** to the next slab by the pack (slab size in the rate card, for example 0.5 kg), using integer grams: `slabs = ceil(chargeable_grams / slab_grams)`. Rounding up is the published convention the pack models, so it is a rule, not an accident.
- Volumetric weight in grams = `ceil(L_cm * W_cm * H_cm * 1000 / divisor)` (divisor from configuration, default 5000; ceil to the gram). Dimensions are Decimals with at most one decimal place (mm).
- If a percentage ever applies (for example a surcharge), the pack computes it in paise with `ROUND_HALF_UP` to the paisa at one named step, and the assessment records the step as a fact. There is none in v1.

## 7. Simulated ledger semantics

The ledger is simulated (D4) but behaves like a strict real one, so the executor is tested against realistic behaviour.

| Operation | Behaviour |
|-----------|-----------|
| `post_credit(req, key)` | Validates: amount > 0, two decimals, reason code known, `amount <= outstanding` for the shipment's charge line. Writes one CREDIT entry with status POSTED and the idempotency key. A repeat with the same key returns the original transaction with `duplicate = True` and writes nothing (`idempotent_writes = True`). A different key for the same shipment and reason while one is POSTED -> `PermanentPortError(409)`. |
| `find_transaction(key)` | Returns the transaction for the key, or None. |
| `get_transaction(ref)` | Full snapshot: id, shipment, amount, reason, type, status, key, posted_at. |
| `get_entries(shipment_ref)` | All DEBIT and CREDIT entries, oldest first. |
| Reversal | Not available to the agent. A human reverses outside the system (residual risk RR4 in `threat-model.md`). |
| Storage | Its own SQLite file (`ledger.sqlite3`), separate from the case database, so a case-database bug cannot fake a ledger entry. |

Failure injection (test configuration only, `modules.ledger.settings.failure_mode`, never on by default):

| Mode | What it does | Executor must end in |
|------|--------------|----------------------|
| `timeout_before` | raises `TransientPortError` without writing | SENT after retry |
| `timeout_after` | writes, then raises `AmbiguousOutcome` | SENT (found by lookup) |
| `error_500_applied` | writes, then raises `AmbiguousOutcome` | SENT (found by lookup) |
| `error_500_not_applied` | raises `AmbiguousOutcome` without writing | SENT after a safe retry (idempotent) |
| `delayed_visibility` | writes; `get_transaction` raises `ExternalNotFound` for N seconds | VERIFIED after VERIFYING rests |
| `wrong_amount` | writes amount + 1 paisa | UNKNOWN `verification_mismatch` |
| `reject_400` | raises `PermanentPortError(400)` | FAILED, case escalates |

## 8. Verification checklist for a credit

All must hold, read from the ledger after the write (executor read-back, ADR 0010):

1. The transaction exists, type CREDIT, status POSTED.
2. Amount equals the approved amount exactly (paise).
3. Shipment ref and reason code equal the intent's.
4. Idempotency key equals the action's.
5. Exactly one CREDIT for (shipment, reason) exists in the ledger.
6. `outstanding_charge` after the credit equals the value before minus the amount.

Any failure -> UNKNOWN `verification_mismatch` -> a person decides (I4).

## 9. Test ideas

- Property (hypothesis): for random rate cards, dimensions and weights, `assess` is deterministic, chargeable weight is `max(dead, volumetric)` rounded up to a slab, and the charge is monotone non-decreasing in every dimension and in dead weight.
- Property: `credit_amount` is never negative for a reason in `definite_errors`, and the total credited on a charge line never exceeds `charged - lowest_proven_correct_charge`.
- `calculation_error` is not proven when the bill used the correct slab (negative test); proven when the bill used the wrong zone.
- `remeasurement_lower` is not proven by a seller's photo or claim; proven only by a structured ops finding.
- Each failure-injection mode ends in the status in section 7.
- Second credit for the same shipment and reason from a new case: refused by policy and by the database.
