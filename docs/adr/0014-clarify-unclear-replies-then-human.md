# ADR 0014: An unclear seller reply gets one clarification, then a person

Status: accepted (owner decision D19, 2026-11-02)
Date: 2026-11-02

## Decision
When a seller's reply is unclear, partial or irrelevant, the agent sends one `clarification_request` that names what is still missing or unclear. If the case is still not resolvable after that (a second clarification would be needed, the reply cannot be handled, or the deadline passes after a reply), the case goes to a person with reason `reply_unresolved`. A seller who replied is never closed as CLOSED_NO_RESPONSE. The seller-side `wait` (T08) is removed.

## Why
The owner's call: a seller who engaged deserves either a useful answer or a person, not a silent close at 72 h. One follow-up resolves most ambiguity ("the photo is missing the tape measure"); a hard limit of one keeps a case from looping and stays inside the contact limits (3 per case, 1 per day).

## Options considered
- Keep waiting until the original deadline, then close as no response (the A1 draft): fewer messages, but treats an engaged seller like a silent one.
- Escalate every unclear reply straight to a person: safest, but more human work for cases a single follow-up would settle.

## Consequences
Easier: no engaged seller is ever closed without an answer. Harder: one more message kind (`clarification_request`, reviewed in the pilot like every kind) and one more config key (`policy.contact.max_clarifications_per_case`, default 1). Watch: the investigator prompt (Stage A4) must prefer `request_seller_evidence` with a precise issue over `wait` after a reply; Stage A2 formalises the policy rule. Checked by state-table check C15.

## Sources
Owner decision in session, 2026-11-02. Project documents (docs/FLOW.md sections 2 and 5a). No external documentation was needed.
