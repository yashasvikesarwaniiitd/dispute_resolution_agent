# ADR 0010: Read-back verification with a bounded retry window; "sent" is not "delivered"

Status: proposed
Date: 2026-11-02

## Decision
After a write, the executor verifies by reading back from the external system and comparing with what we stored. If the result is not yet visible, the case rests in VERIFYING and retries on a schedule (0, 5, 30, 120, 300 s) until a 10-minute deadline, then becomes UNKNOWN. For email, "verified" means present in Sent Items with the approved text and the right recipient; a later bounce escalates the case.

## Why
External systems are eventually consistent: a sent message may take seconds to appear. One immediate read-back would turn normal lag into UNKNOWN outcomes and flood human review. A bounded window keeps I4 (never assume success) without false alarms. Being explicit that email verification cannot prove the seller received the message avoids over-claiming in the audit trail.

## Options considered
- Synchronous read-back with short retries inside the step: blocks a worker for minutes.
- Trusting the write response: violates I2's independent read-back.

## Consequences
Easier: realistic behaviour against Graph and Jira. Harder: VERIFYING becomes the one automatic state that can rest. Watch: the deadline and schedule are configuration (A3 config.md).

## Sources
Project documents only. Graph and Jira consistency behaviour is verified against official documentation in Stage A3 (integrations.md).
