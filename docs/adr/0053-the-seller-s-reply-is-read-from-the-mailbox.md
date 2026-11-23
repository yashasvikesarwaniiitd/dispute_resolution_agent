# 0053 — The seller's reply is read from the mailbox

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "built and test e2e and deliver me properly"). Builds on ADR 0038 (Gmail), ADR 0022 (reading the mailbox) and ADR 0052 (the hub's reply).

## The problem

The agent emails a seller, for example to ask for a photo, and the case waits for the reply. The mail adapter could read the inbox (`fetch_replies`), but nothing in the running app called it. A real reply sat in Gmail; the case sent a reminder and closed as "no response". The backlog recorded it: "a real Gmail reply ... not yet exercised live".

Two facts of the hosted setup made a naive reader dangerous or useless:

- The test seller is a `+seller` alias of the agent's own Gmail account. Every email the agent sends the seller also lands in that inbox. Read as replies, they would make the agent answer itself.
- A person replying from that Gmail account replies from the plain address, not the `+seller` alias. Routing compared addresses exactly, so it would have refused every real reply.

## Decision

1. **A job reads the mailbox** (`api/seller_replies.py`) on the runner's tick, every minute, and submits each message as SELLER_REPLY_RECEIVED (a bounce as DELIVERY_FAILURE_RECEIVED). The message id is the dedupe key. Routing is dispatch's own: the thread, or exactly one open case for that sender and a shipment named in the subject. A message that matches no case is kept as UNROUTABLE for a person, never guessed onto a case.
2. **The agent's own email is never a reply.** The Gmail adapter skips any message carrying the agent's idempotency header or Message-ID. A reply has its own Message-ID; ours appears only in its In-Reply-To.
3. **One Gmail mailbox matches itself** (kernel: `domain/mailbox.py`, used by `dispatch._route_reply`). For `gmail.com` and `googlemail.com` only, the plus part and dots of the local part are ignored, as Google documents. Every other address is still compared exactly.
4. **`gmail` is a known inbound source** (kernel: `domain/events.py`, and `docs/spec/events-catalog.md`), so a Gmail reply is labelled as one.
5. **The person sees what came back.** The With-a-person card gains "What came back": the hub's answer and the seller's reply, newest first, with their words exactly as recorded and the mail client's quoted history left out. The timeline quotes the seller's reply the same way. Their words remain untrusted text (I5); no figure is ever read from them (I9).

## Why

A waiting state that nothing can end except a timeout is a broken promise to the seller. Reading the mailbox closes it, and the existing routing rules already say what to do with a reply that fits no case. The Gmail rule is the narrowest change that makes the test seller's real replies count. It does not loosen matching for any other provider, where a plus part or a dot may name a different person.

## Options considered

- **Make the test seller a different, real address.** Cleaner for testing, but it changes the owner's hosted secrets and leaves the same bug for any Gmail seller with an alias.
- **Rewrite the sender to the case's contact in the job.** It would hide the real sender from the audit trail. Rejected.
- **Let a model read replies.** The hosted app runs the rules engine; a reply it cannot interpret goes to a person, which is what the card is now for.

## Consequences

- A case waiting on the seller moves within about a minute of a reply. With the rules engine, an unread reply sends the case to a person, who sees the reply on the card.
- Kernel changes: `domain/mailbox.py` (new), `orchestrator/dispatch.py` (the two sender comparisons), `domain/events.py` (one known source). No transition, rule or invariant changed.
- Tests: `tests/unit/domain/test_mailbox.py`, `tests/unit/modules/email/test_gmail_api.py` (own email in the inbox), `tests/unit/api/test_seller_reply.py` (reply to the case, the card, a stranger kept but not routed, read twice once), `tests/unit/api/test_hub_answer.py` (the hub's answer on the card).

## Sources

- Google, "Use an alias in Gmail" (plus addressing; dots are ignored): https://support.google.com/mail/answer/12096
- Gmail API, users.messages.list and get: https://developers.google.com/gmail/api/reference/rest/v1/users.messages
