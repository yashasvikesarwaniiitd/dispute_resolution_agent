# ADR 0009: A free-text withdrawal needs human confirmation

Status: proposed
Date: 2026-11-02

## Decision
Only a structured, authenticated `DISPUTE_WITHDRAWN` event (from the seller portal) moves a case to WITHDRAWN on its own. A withdrawal written in an email is read as evidence; policy escalates it with reason `seller_withdrawal_claimed`, and a human confirms with `HUMAN_CONFIRMED_WITHDRAWAL`.

## Why
FLOW.md section 2 says "seller withdraws in writing" moves to WITHDRAWN. But I5 says outside text can never change state. An AI reading of an email is exactly the path a spoofed or injected message would use to close someone's legitimate dispute, and WITHDRAWN can only be reopened by a human. Invariants beat FLOW (CLAUDE.md section 18), so the sketch changes rather than the invariant weakening.

## Options considered
- Accept the AI's `withdraws_dispute` reading when confidence is high and no manipulation flags are set: still lets text change state; confidence is not authentication.
- Ignore free-text withdrawals: the seller's request would be lost.

## Consequences
Easier: no injection path to closing a case. Harder: a little human work for genuine email withdrawals. Watch: in the pilot every message is human-reviewed anyway; revisit only with an ADR if a verified-sender mechanism appears.

## Sources
Project documents only (CLAUDE.md section 3, docs/FLOW.md section 2). No external documentation was needed.
