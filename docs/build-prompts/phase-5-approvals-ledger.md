# Phase 5: Approvals, simulated ledger and verified credits

## Role
Principal engineer making money movement safe and provable.

## Context
Reasoning works and proposes. Read `docs/FLOW.md` and the wallet-sequence assumptions recorded in the Phase 2 ADR. The ledger here is a simulation and never touches a real wallet.

## Objective
A credit can only happen after a stored human approval, is posted idempotently to a simulated ledger, and is independently verified before the case can resolve.

## Scope
### In scope
Ledger simulator with its own store, credit approvals in the existing review queue, the credit flow end to end, failure injection, full single-case demo.
### Out of scope
Cross-case behaviour, real wallets, production hosting.

## Tasks
1. Ledger simulator behaving like a real ledger: its own SQLite store, transaction ids, `post_credit` idempotent on the key, `get_transaction`, and the four transaction types. Support injected timeouts, duplicates and ambiguous results.
2. Contract suite for `Ledger`; run it against the simulator.
3. Extend the Phase 3 approvals module to credits: the review queue lists pending credit approvals with the evidence summary and the calculated correction, and records approver, time and reason.
4. Credit flow: proposal `propose_credit` with a reason code, then policy (always requires approval in the prototype), then `AWAITING_APPROVAL`, then `EXECUTING` on a valid approval, then `VERIFYING`, then `RESOLVED_CREDITED`.
5. Verification: the transaction exists; amount and currency match; it belongs to the right seller and case; there is no duplicate; the ledger reports success. Any failure or uncertainty goes to `NEEDS_HUMAN_REVIEW`.
6. Reason codes and limits from `policy.yaml`; an amount that differs from the deterministic expected correction is rejected by policy.
7. Registry rule: `ledger` requires `approvals` (already enforced; add a scenario test).
8. Extend `make demo` to run the single-case sequence: dispute, evidence, recommendation, real email, real reply, resume, real Jira issue, approval, ledger credit, verification, timeline.
9. ADRs: approval record design, ledger idempotency, ambiguous outcome handling.
10. Add the upheld-charge and credit-confirmation templates, and confirm both go through the Phase 3 message review queue. Which message kinds need review stays a setting in `policy.yaml`.

## Technical requirements
- Credits cannot be executed without an approval record, even if code calls the executor directly (negative test).
- The ledger simulator has no code path shared with the kernel's logic, so verification is independent.

## Acceptance criteria
- Replaying the same event and retrying the same action never produces a second credit (tests).
- Executing a credit with no approval, or with an amount above the limit, is refused (negative tests).
- A ledger timeout produces UNKNOWN and then `NEEDS_HUMAN_REVIEW`; the case never resolves (test).
- A rejected approval returns the case to `INVESTIGATING` (test).
- `credit_confirmation` and `upheld_explanation` drafts wait in the Phase 3 review queue and are sent identical to the approved text (tests).
- `make demo` runs the full single-case sequence and the timeline shows every decision, approval, verification and final state.

## Deliverables
Ledger simulator, approvals module and UI, flow code, tests, demo, ADRs.

## Constraints
I2, I3, I4, I10, I11. Label the ledger as simulated in the UI and in the README.

## Process
Plan mode first. Show the approval screen layout in the plan.

## Model and effort
Plan in Opus at effort high; build on Sonnet. The `safety-reviewer` subagent review is mandatory because this phase moves money. Do not finish without it.

## Stop and ask if
A verification check cannot be made independent of the write path.
