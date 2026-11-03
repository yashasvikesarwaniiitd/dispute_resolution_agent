# 0028 — Implementation notes against the Phase 3 decisions

Date: 2026-11-03
Status: accepted
Context: card P3-11. `CLAUDE.md` section 12 asks for notes where implementation met a
decision, and a new ADR only where implementation **changed** one.

No accepted decision was changed. ADRs 0010, 0013, 0018, 0019, 0020, 0022 and 0023 were all
implemented as written. What follows is what each cost in practice and where the design
turned out to be load-bearing, so the next person does not have to rediscover it.

## 0019 — email is a marked draft, sent, then found by its key

Implemented exactly. Two things the decision did not say, and which the implementation had
to settle:

**A 404 on a mail folder is not a missing message.** `sentitems` and `drafts` exist on every
mailbox, so a 404 there means the mailbox or the permission is wrong. The first version let
that surface as `ExternalNotFound`, which `find_sent` is not allowed to raise and which
would have read as "nothing was sent". It is now a permanent error naming the folder.

**"Not in Sent Items" has to be treated as "not yet".** The executor's `verify` caught only
`TransientPortError`, and `ExternalNotFound` is what both real adapters raise for the first
seconds of every ordinary send. That gap would have killed the step on every successful
message. It now rests and becomes UNKNOWN at the deadline, which is what ADR 0010 describes.

## 0020 — Jira idempotency comes from our own record

Implemented, and the decision earned itself twice over. The adapter never searches before
creating, and `idempotent_writes = False` is what tells the executor a retry is unsafe.

The implementation surprise was elsewhere: Jira's description round trip is **not** lossless
unless you make it so. Two separate bugs — paragraph breaks lost, then leading indentation
stripped — would each have failed verification on a perfectly correct issue, because the
executor compares what came back with what was sent. Neither was Jira's fault; both were
differences the adapter introduced itself. Hypothesis found the second after every
hand-written example passed.

## 0022 — polling, not webhooks

Implemented. The routing rules in `orchestrator.md` section 2 turned out to carry more
weight than the transport decision: "exactly one open case matching both the sender and a
shipment reference" is what stops a reply being attached to the wrong dispute, and the
tempting shortcut — the only open case for that seller — is wrong the first time a seller
has two.

## 0023 — personal Outlook.com, delegated sign-in, encrypted cache

Implemented as written, including the part that is easy to skip: **no plain-text fallback**.
`msal-extensions` offers one when the platform keystore is unavailable, and taking it would
turn a ninety-day refresh token into a readable file. The adapter raises instead.

A failed silent refresh is an `AuthPortError`, never transient. Retrying a revoked token
gets the same answer every time while the case waits.

## 0013 and 0018 — the approvals table is the truth, and decisions are authenticated

Implemented. The `ApprovalService` can only mark an approval notified; a test walks its
methods to confirm none returns a decision, because a port that could would be a second
source of truth about who said yes.

ADR 0018 is enforced structurally rather than by validation: `ReviewConsole` takes the
reviewer at construction from the signed-in session, and `approve` and `reject` have **no
parameter** an incoming request could use to claim a different one.

## 0010 — "sent" is not "delivered"

Implemented, and worth restating because it is the claim a demo invites you to overstate.
Verified means the message is in Sent Items with the approved text. Nothing at this end
knows whether it arrived; that is what the bounce poller is for, and bounce detection is
itself a documented heuristic (risk K2).

## Consequences

- Phase 4 inherits two adapters whose request shapes are asserted against a simulated
  service, so a change to either fails locally rather than against a live account.
- The gap recorded in `BACKLOG.md` — `investigate` has no reasoner, so `make demo` cannot
  complete its documented run — is the one phase-exit line Phase 3 does not meet.
