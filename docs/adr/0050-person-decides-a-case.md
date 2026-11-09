# 0050 — A person decides a case: verdicts on the With-a-person desk

Date: 2026-11-10
Status: **accepted** (owner, 2026-11-10: "Yes, add verdicts" and "Decide, then approve"; D72). Builds on ADR 0046 (the case desk) and ADR 0049 (the image check). Changes the frozen spec under this ADR: `docs/spec/transitions.yaml` and `docs/spec/schema.sql`.

## The problem

A case reaches a person for a reason code names (32 review codes; in the demo, B5 photo or history, R21 suspected manipulation, R14 two proven errors). The desk offered three things: take over (the case ends in `HANDED_TO_HUMAN`, outside the system), return to the agent (which, for B5, sends it straight back) and confirm a withdrawal. Nobody could record a verdict. The card also showed little of what the person needs to judge: no declared against measured size, no code's figures, no task.

## Decision

1. **A person decides from code's menu.** A new person event, `HUMAN_DECIDED_CASE`, carries only the choice: uphold the charge, credit a code-proven error, ask the hub to remeasure, or ask the seller for evidence (from the closed list, ADR 0043). It never carries an amount or message text, and a note saying why is required.
2. **It is acted on exactly as a proposal.** The `human_verdict` step records the choice as a `Proposal` with `reasoner="human"` and `decided_by` (the person), then calls the investigate step's own `_act_on`: code derives the credit amount from the assessment (I9), the pack drafts the message, and the same policy rules run. The menu is the one a proposal would have (`allowed_actions` at INVESTIGATING), without escalate and wait.
3. **Decide, then approve.** A credit or a message waits for an approval of its exact amount or text (T49), like the agent's; the same person approves it in "Awaiting your approval". Only a non-credit action policy allows goes straight to planning (T50, `intent_is_not_credit`, C7). The executor, read-back verification and ledger are unchanged.
4. **R14 lets a person choose between two proven errors.** R14 escalated any credit on a case with two definite errors because "two proven errors are a person's call". A person's proposal has made that call, so R14 allows it - the reason must still be proven, the amount is still code's, and the approval still follows. The approval step now looks up the proposal behind a plan, so R14's re-check at execution sees who decided.
5. **A refusal leaves the case with the person.** If policy or code refuses the choice (for example a credit where code proved no error), the step rests with `HUMAN_DECISION_REFUSED` and the reason on the trail. The case never moves somewhere the person did not choose.
6. **Spec changes.** `transitions.yaml`: step `human_verdict`; T49 (`NEEDS_HUMAN_REVIEW -> AWAITING_APPROVAL`, `verdict_require_approval`, guard `actor_is_human`) and T50 (`-> EXECUTING`, `verdict_allow`, guards `actor_is_human`, `intent_is_not_credit`, `action_planned`); the accept matrix row for the new event in every state. `schema.sql`: the two closed lists gain `HUMAN_DECIDED_CASE` and `human`. Migration `0003_human_verdict` widens them on existing databases in place (no row changes; integrity check after).
7. **The desk shows what to review.** Each card states the task for its reason code, the case facts (declared and measured size and weight, chargeable weight, charged, code's correct charge, the difference), what to check, the photo and the photo check, and the choices with what each one does.

## Why this way

- **The person replaces the reasoner, nothing else.** Every safety rule that guards the agent's proposal guards the person's: I3 (approved, code-derived credit), I9 (numbers from stored facts), I13 (approval bound to the text hash). A separate "human credit" path would need each re-proven.
- **No new money rule.** On the photo cases code proves no overcharge (the hub's measurement stands). The genuine next step when a seller disputes the hub's measurement is a remeasurement: if it comes back lower, code proves `remeasurement_lower` and the credit follows the normal path. A person's judgement never sets an amount.

## Options considered

- **Let the person type an amount.** Rejected: breaks I9 and makes the approval meaningless.
- **Credit the charge on the seller's declared size.** Deferred: a new, judgement-based credit reason; remeasurement gives the same answer with code's proof.
- **The verdict itself as the approval (one click).** Rejected by the owner: it would be a second approval path for I3 and I13.

## Results

- **Every demo case with a person, decided end to end** on the real runner (`tests/e2e/test_human_verdict.py`, 12 tests):
  - uphold on SHP-000001 (photo), SHP-000004 (history) and SHP-000006 (manipulation): T49, the upheld explanation is approved, sent and verified, `RESOLVED_UPHELD`;
  - remeasure on SHP-000001: T50, the ticket is raised and verified, `AWAITING_OPS`;
  - ask the seller on SHP-000001: the evidence request is approved and sent, `AWAITING_SELLER`;
  - SHP-000010, two proven errors: credit the calculation error (₹1,820.00) or the duplicate charge (₹2,800.00), each code's amount, approved, posted, verified, `RESOLVED_CREDITED`;
  - a credit code did not prove (SHP-000001) is refused: the case stays with the person, HUMAN_DECISION_REFUSED names `credit_reason_unproven`;
  - a decision forged on the desk's channel with a system actor is ignored (P3);
  - Agent steps shows "A person decided: ..." with the note as recorded, and the agent's proposals keep their rounds;
  - migration 0003 widens an old database, refuses junk after, and is idempotent.
- **The card** (`tests/unit/ui/test_case_sheet.py`, 9 tests, and two Review-screen tests): the task by reason, declared 20 × 15 × 10 cm · 480 g against measured 30 × 22 × 15 cm · 1,100 g, no credit where code proved none, one credit per proven error at code's amount, seller text escaped.

## Sources

- SQLite, ALTER TABLE, section 7 "Making Other Kinds Of Table Schema Changes": https://www.sqlite.org/lang_altertable.html
