# 0027 — Every figure in the weight-dispute pack is configuration, not code

Date: 2026-11-03
Status: accepted
Context: card P2-11, decisions D2 and D10, `CLAUDE.md` section 12

## Decision

No tariff number appears anywhere in the pack's code. The volumetric divisor, the slab size,
the per-zone rates, the packaging exemptions and the baseline's size tolerance all live in a
`RateCard` and a `BaselineConfig`, both of which are data the calculator is handed. The
calculator knows the *shape* of the arithmetic — volume divided by a divisor, weight rounded
up to a slab, slabs multiplied by a rate — and knows none of the values.

This is enforced rather than intended. `tests/unit/packs/test_calculator.py` walks the
calculator's syntax tree and fails if any numeric literal that could be a rate or a divisor
appears in it. The one constant that is in the code is `GRAMS_PER_CM3_NUMERATOR = 1000`,
which is the definition of a cubic centimetre of water, not a commercial figure.

## Why this matters more than it looks

This project is public, on synthetic data, and models a workflow that logistics companies
run. The line it must not cross is appearing to reproduce any particular company's pricing.
A zone rate hard-coded in a function reads as "this is what it costs". The same number in a
config file next to a version string reads as what it is: an illustrative value chosen so a
worked example comes out to a round figure.

So the separation is a claim-control measure first and a design choice second. It is what
lets `release-gates.yaml`'s `claims_not_to_make` list be honest, and it is why the rule is
"no literal in the calculator" rather than the weaker "keep the rates together".

## What is public, and what is ours

**Public convention, modelled faithfully:**

- *Volumetric (dimensional) weight.* That carriers bill the greater of actual weight and a
  volume-derived weight is published practice across the industry, as is computing it as
  `L x W x H / divisor`. The divisor itself varies by carrier and by whether the dimensions
  are in centimetres or inches, which is exactly why it is configuration: 5000 is a widely
  published value, not the value.
- *Slabbing.* That weight is rounded **up** to the next band, and that a shipment occupies at
  least one band, is published convention. `money-rules.md` section 7 states it as a rule so
  that it is a decision rather than an accident of how a division was written.
- *Zone-based pricing.* That distance bands cost different amounts is universal. The band
  letters A–E are a generic scheme.

**Our assumptions, invented for this project:**

- Every rupee figure. The zone rates (₹30 to ₹70 per slab), the 500 g slab, the 5,000
  divisor, the 5% baseline tolerance. These were chosen so that the worked examples in
  `synthetic-data.md` come out to checkable numbers — the kurta at 1,980 g billing exactly
  four slabs at ₹38 — and for no other reason.
- Every threshold in `policy.yaml`: contact limits, approval expiry, retry intervals, the
  reopen window. These are product judgements about how an agent should behave, not industry
  facts.
- The fault vocabulary, the zone distribution in the generator, the packaging types.

**Deliberately absent:** any real carrier's published rate card, any real seller, any real
shipment. The generator produces contacts on `example.invalid`, which cannot resolve, so a
generated address can never reach a person (I11).

## Consequences

- A reader can change what the system charges without touching a line of Python, which is
  the test of whether this decision was actually implemented.
- A second pack — a different dispute kind, or the same one on a different carrier's
  conventions — supplies its own card and reuses the calculator unchanged.
- The AST test is a maintenance cost and worth it. An earlier version of it string-matched
  digits in the source, which passed while flagging its own docstrings; the rewrite checks
  the parsed tree. A test that enforces this rule has to be a test that cannot be fooled by
  a comment.
- The project can state publicly that it models a public workflow with illustrative values,
  and point at the config files as the evidence.

## Alternatives considered

**Constants in the calculator, documented as illustrative.** Simpler, and it fails the only
thing this ADR is for: a number in code is a number someone will quote. Rejected.

**A rate card loaded from a remote service.** Realistic, and out of scope — there are no
cloud resources in this project (`CLAUDE.md` section 16), and it would add a failure mode to
the one calculation that must never be unavailable. Rejected.
