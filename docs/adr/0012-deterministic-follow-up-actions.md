# ADR 0012: Deterministic follow-up actions; "resolved credited" means credited and told

Status: proposed
Date: 2026-11-02

## Decision
Some actions are followed by a second action chosen by code, not the AI: a verified credit is followed by a `credit_confirmation` message, and a human taking a case may trigger a `handed_to_human_notice`. These follow-ups are intents with source FOLLOW_UP and go through policy and approval like any other. The case reaches RESOLVED_CREDITED only when the confirmation is verified. A rejected or blocked follow-up escalates instead of re-planning.

## Why
Both messages are kinds that need review in the pilot (D6), so they need states to wait in; sending them from a terminal state would mean acting on a closed case. Making the follow-up deterministic keeps the AI's role to one proposal per decision (D5). Escalating on a rejected follow-up (instead of returning to INVESTIGATING) prevents re-planning a case whose money has already moved.

## Options considered
- Post-outcome notifications outside the state machine: no place for approval; breaks I8.
- One approval covering both the credit and the confirmation text: couples two subjects in one record and complicates the I13 hash check.

## Consequences
Easier: every money outcome reaches the seller through the same verified path. Harder: two approvals per credited case in the pilot. Watch: if the confirmation fails, the credit is already posted; the case escalates and the human sees the verified credit in the timeline.

## Sources
Project documents only (docs/FLOW.md section 5a, docs/HANDOFF.md D5-D6). No external documentation was needed.
