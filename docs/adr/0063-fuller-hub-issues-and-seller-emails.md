# 0063 — Fuller hub issues and seller emails

Date: 2026-11-25
Status: accepted. The owner asked for the Jira issue and the emails to be descriptive, with the data and measurements in them.

## Context

- **The hub's Jira issue** named the shipment and asked for a re-measurement, but gave the hub nothing to find or check the parcel with.
- **The seller's emails** gave one or two figures. The upheld email called the chargeable weight "a size-based weight", which is not always true. The credit confirmation did not say why the seller was owed the money.

## Decision

**1. The Jira issue carries the hub's own record** (`WeightDisputePack.hub_issue_details`, called optionally from `investigate._payload`, the same pattern as `remeasurement_brief`):
- the measurement on record (size and weight), the scanner, the packaging booked, and the charge it led to (slabs, rate, zone, amount);
- all from trusted evidence values, never outside text (I5).

**Deliberately left out:** the seller's declared size, and the history's usual size. A hub shown the figure it is meant to check can copy it back as its "re-measurement"; ADR 0060 kept figures out of the scanner-audit question for the same reason.

**2. The emails show the bill worked out** (`templates/_working.md.j2`, included by the evidence request and the upheld explanation):
- what the seller declared and what the hub measured (size and weight);
- the size-based weight and its divisor, the charged weight, the slabs and their size, the rate, and the charge;
- what the declared parcel would have cost.

The credit confirmation now says what was charged and, in words, why the credit is owed (the same charge taken twice; the hub's re-check found the parcel smaller; the charge did not match the rates).

**3. Every figure is still a fact checked by R09** (I9). The new facts come from `WeightDisputePack.assess`:
- `f_declared_*` and `f_measured_*`: the sides and weight;
- `f_size_weight`, `f_slabs`, `f_rate_per_slab` and `f_declared_charge`;
- `f_size_divisor` and `f_slab_weight`, which are rate-card constants.

Each fact cites the evidence item it came from, apart from the two constants. A line whose facts a case lacks is left out (`has(...)` in the template) rather than failing the whole email.

**4. Two kernel changes, both small:**
- **`Fact.unit` ("cm" or none).** A side renders as "34 cm", and R09 checks a "cm" token against a centimetre fact. "34 g" against the same fact is refused.
- **R09 reads a stored Decimal fact.** A Decimal fact read back from storage arrives as its text ("30"), because JSON has no decimal type. R09 now reads plain-digit text as the number. This was found by rendering S67 end to end: its upheld email was refused on "30 cm" and the case went to a person.

The credit confirmation's draft is also passed the credit's reason code (`execution._confirm_credit`).

## Open question for the owner

The issue offers "Declared size confirmed" (ADR 0062) but, by the decision above, does not show the declared size. A hub can use that reply only if its own system shows the booking. The options are to show the declared size and accept the copy-back risk (a credit still needs a reviewer), or to keep it hidden and expect re-measurements.

## Tests

- `tests/unit/policy/test_r09_numbers.py` (`TestLengthsInCentimetres`):
  - a side in cm is justified, including one read back from storage;
  - the same number in grams is refused, and so is a different length;
  - a word fact never becomes a number.
- `tests/unit/packs/test_templates.py`:
  - the upheld email shows the full working when the facts exist and only the charge when they do not;
  - the credit confirmation cites the charge;
  - shared parts are exempt from registration but not from the no-literal-digit check.
- `tests/e2e/test_history_reaches_hub_and_model.py`: S62's issue shows the measurement on record, the scanner, the packaging and the charge, and never the declared 19 x 11 x 2.
