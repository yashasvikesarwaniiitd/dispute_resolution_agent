# 0065 — Audit fixes: hub figures, duplicate-refund netting, the approval preview

Date: 2026-11-25
Status: accepted. The owner chose to fix the three must-fix findings of the whole-repo audit before building the Evals dashboard.

## Context

The audit found two ways the money could come out wrong, and one way the reviewer could approve figures they were not shown correctly.

1. **A hub reply with two decimals upheld the charge.** The reader accepted `25.55 cm`, but the rate card measures to the millimetre (one decimal) and refused it. The pack swallowed that refusal, so nothing was proved, while the finding still said "remeasured", so rule B6a upheld the charge. Grams were also truncated: `500.5 g` became 500 g, one slab too low at a slab edge.
2. **A duplicate-debit refund reduced the overcharge credit.** `credit_amount` netted every earlier credit on the shipment. Charged ₹2,800 twice, correct ₹980: with the duplicate (₹2,800) credited first, the overcharge credit was 2,800 − 980 − 2,800, which is less than zero, so nothing was paid. The other order paid ₹4,620. The seller lost ₹1,820 depending on which credit a person picked first.
3. **The Review screen's email preview could lose a figure.** Each figure was swapped for a placeholder `\x00<i>\x00` and swapped back later. A later figure such as the slab count "6" matched inside the sixth placeholder. The sent email was always right; the preview the reviewer approves was not.

## Decision

1. **`modules/ticketing/hub_comment.py`:**
   - A side with a real hundredth (`25.55`) is not an answer. It is stored as an unreadable note, which reaches a person and never upholds.
   - `25.50` reads as `25.5`.
   - Grams round up.
2. **`kernel/policy/credit.py` (kernel):**
   - The calculation-error and remeasurement credit nets only earlier non-duplicate credits: `already_credited − already_credited_duplicate`. Both orders now pay ₹4,620.
   - R16 (never more than is outstanding) is unchanged and still bounds the total.
   - The test-only copy in `packs/weight_dispute/reconcile.py` gets the same change, so the two do not disagree. Deleting that copy is audit finding 15, not done here.
   - `docs/spec/money-rules.md` section 3 is updated.
3. **`ui/review_view.py`:** placeholders contain no digits (a private-use character), so no figure can match inside one.

Each fix has a test that fails on the old code.

## Not done

The other 17 audit findings. "Database is locked" (finding 4) is the next one before showing the demo to several people at once.
