# ADR 0016: Credits only for reasons code proved from evidence; no amount caps, structural limit only

Status: accepted (owner decisions D29, D30, 2026-11-02)
Date: 2026-11-02

## Decision
A credit may be proposed only for a reason code that the domain pack proved from evidence (`definite_errors`: calculation error, lower hub remeasurement, duplicate charge), for exactly the amount code computed, and at most once per shipment and reason. The AI's `propose_credit` is a request, never the basis. There are no configured amount caps; the structural limit is that a credit cannot exceed the outstanding charge. A human approves every credit.

## Why
The owner's calls. Letting model judgement be the basis for paying money would make the most expensive decision depend on the least verifiable component, and would trigger the stage's stop-and-ask condition. Restricting credits to code-proven reasons keeps the AI useful (it finds what to investigate and requests remeasurements) while keeping money deterministic. Caps were declined because every credit is already human-approved and exact; the outstanding-charge limit is what makes I3's "amount within limits" true.

## Options considered
- AI judgement as a credit basis with human approval: more flexible, but model output would drive a financial write.
- Caps (for example ₹500 escalate, ₹2,000 deny): declined by the owner for the pilot; can be added later as configuration plus a rule.

## Consequences
Easier: every credit is reproducible from evidence. Harder: a genuinely unfair charge that no rule can prove goes to a person rather than being credited by the agent. Watch: new reason codes need a pack rule and an ADR.

## Sources
Owner decisions in session, 2026-11-02. docs/spec/money-rules.md.
