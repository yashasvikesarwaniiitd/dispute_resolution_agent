# ADR 0015: Verdicts form an ordered lattice; the strictest wins; model output only raises

Status: accepted (owner, checkpoint D28, 2026-11-02)
Date: 2026-11-02

## Decision
Verdicts are ordered ALLOW < REQUIRE_APPROVAL < ESCALATE < DENY and combined by max over every rule result and caution-signal floor. All rules run on every evaluation. Rules may read model output only to raise a verdict. Five rules are mandatory (R02 kill switch, R03 module availability, R10 message approval at execution, R13 credit approval, R17 duplicate credit); the engine refuses to start without them. A rule that raises becomes DENY.

## Why
"Strictest wins" makes safety a property of arithmetic instead of careful coding in each rule: adding a rule or a signal can never make the system less careful, which is exactly I7 and I15. Combined with R13, it gives a short proof that a credit can never be ALLOW (policy-rules.md section 2). Running all rules keeps a complete record of every objection for the approver.

## Options considered
- First-match rule chains: order-dependent and easy to break by inserting a rule.
- Scored or weighted rules: hard to reason about; a high score elsewhere could offset a hard "no".

## Consequences
Easier: proofs and property tests (TC-POL-P1..P6). Harder: a rule cannot "approve" anything; relaxing behaviour means changing configuration (for example which message kinds need review), not adding a permissive rule.

## Sources
Project documents (CLAUDE.md I7, I15; docs/spec/kernel-interfaces.md section 7). No external documentation was needed.
