# 0033 — The inbound half: dispatch, transition effects, timers and the reply round

Date: 2026-11-04
Status: accepted (built under the owner's instruction "complete everything", gaps before Phase 6)
Implements orchestrator.md sections 3, 6 and 7, transitions.yaml effects, `accept` and P1-P4.

## Context

Until now a case could only be pushed forward from intake. Nothing took a seller's reply, a
fired timer, an ops finding, a withdrawal or an approval decision and fed it back into a case,
and nothing carried out a transition's effects. Three of those effects are safety rules, not
bookkeeping: an escalation must mark in-flight actions UNKNOWN (I4), must withdraw a plan still
waiting for approval, and must cancel the case's timers.

## Decisions

1. **One landing path for every move.** `Orchestrator.land` writes the transition's event,
   saves the case, then runs `TransitionEffects` in the same unit of work. Steps, escalations
   and inbound reactions all go through it, so no caller can save a move and forget its effects.
2. **Effects in the spec's order**, each writing an event: cancel (and void fired-but-unhandled
   timers of those kinds, so a late delivery is stale under P1), schedule (delays from policy),
   supersede or expire the pending plan (only one not yet turned into an action), mark in-flight
   actions UNKNOWN, record the outcome to `outcome_outbox` (delivered after commit by the runner).
3. **The dispatcher is a reading of the table.** Route, pre-dispatch checks (stale timer, stale
   approval, non-human on a human event, superseded case, withdrawal by someone else, approval on
   text the reviewer did not see), then the acceptance matrix's alternatives in order. Nothing is
   dropped: no case means UNROUTABLE, ignored events are appended with their reason, deferred
   events are redelivered when the case moves on (same type keeps arrival order).
4. **A fired timer is consumed before its reaction runs**, so a transition that cancels timers of
   the same kind cannot leave a timer both voided and handled. A voided timer is never consumed.
5. **Typed inbound payloads** (events-catalog.md 2.1). `InboundEvent.payload` is serialised as its
   concrete type; before this the inbox stored `{}` for every payload and a reply lost its body.
6. **The reply round.** A reply moves a case because a reply arrived and the table allows it; what
   it says is read later, in the next gather round, by the interpreter, into an evidence item
   whose text stays `UntrustedText` and whose reading can only add caution. No interpreter, or a
   failed one, gives a FAILED item and a person decides. After a reply a second ask is a
   clarification, limited to one by the new R12 (D19). `wait` is honest only while ops is still
   remeasuring (R19, T09).
7. **The contact counters are real** (reply in round, clarifications sent, ops outstanding), and
   message counts are given to policy at planning, so an over-limit message is refused before it
   reaches the review queue rather than after it is approved.
8. **Step notes.** A step may ask the loop to write audit events (INTENT_CREATED,
   APPROVAL_REQUESTED, REMINDER_SKIPPED); they are written even when the step rests.
9. **One assembly** (`api/line.py`) and a **runner** (`api/runner.py`): tick (fire due timers,
   dispatch pending inbox rows, advance stuck automatic cases, deliver outcomes) and start-up
   recovery in the spec's order. Each unit of work is its own transaction.
10. **System approval decisions carry no decider.** EXPIRED and SUPERSEDED are written with no
    `decided_by` and no `decided_at`, as the domain model requires; the moment is on the
    APPROVAL_SUPERSEDED event.

## What the safety review changed

Two HIGH, six MED, all fixed with tests in `tests/e2e/test_inbound_review_fixes.py` that fail
against the first version:
- **H1** a "transition | defer" reaction whose guards failed was redelivered from inside its own
  redelivery, forever. Redelivery is now a separate, bounded pass that never retries a row held
  again in the same pass.
- **H2/M4** a person could never return a case with an UNKNOWN action: the resolution wrote no
  human verifier and the wrong code. It now names the person, uses `human_confirmed_not_done`,
  writes UNKNOWN_ACTION_RESOLVED, and a refused return is ignored on the trail with its reason.
- **M1** held events are redelivered after every move, including the line's own advances.
- **M2** a reply names its shipment as a whole token (SHP-1 is not SHP-10); a thread match also
  needs the case's own seller as sender.
- **M3** a wait runs the caution rules (R20-R22), and an unread reply is never "harmless".
- **M5** a transient failure leaves the event waiting; any other failure marks it FAILED and
  sends its case to a person, so a fired deadline is never lost silently. FAILED never
  overwrites PROCESSED. Logs carry the error type only, never its text.
- **M6** events are written as each move lands, so a mid-advance checkpoint never commits a move
  without its event; a redelivered input event reuses its deterministic id.

## Consequences

- 21 end-to-end tests drive these paths: silence to reminder to CLOSED_NO_RESPONSE, reply to a new
  round, reopen inside and outside the window, approval expiry, decisions on the wrong text,
  withdrawals by the wrong principal, stale timers, ops finding and ops timeout, restart recovery.
- The hand-off notice to the seller (`handed_to_human_notice`) has no template yet: a reviewer who
  asks for it gets NOTICE_NOT_SENT on the trail and still owns the case (BACKLOG).
- The long-running API runtime still assembles Phase 1 steps; wiring it to `Line` and `Runner`
  moves into Phase 6 with the scenario simulator, which needs the same assembly (BACKLOG).
