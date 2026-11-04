# 0032 — Phase 5: the ledger, the stations after approval, and how a credit is proven

Date: 2026-11-04
Status: accepted (built under the owner's instruction "complete Phase 4+ and Phase 5")
Implementation notes for ADR 0012 (credited means credited *and told*) and ADR 0013 (approvals).

## 1. The simulated ledger is independent, idempotent and strict

`modules/ledger/simulated` has its own SQLite file, its own rows and its own arithmetic in paise.
It shares no code with the kernel's money logic (`kernel/policy/credit.py`), so verification
compares two independently produced answers. It validates like a real ledger: positive amount, a
known reason, never more than is outstanding; the same idempotency key twice returns the first
transaction marked `duplicate`; a second key for the same shipment and reason is refused (409).
Every failure mode in money-rules.md section 7 is injectable and tested, each against the outcome
the executor must reach.

## 2. An ambiguous post is settled by lookup, never by retry

A timeout or a 500 after posting is `AmbiguousOutcome`; the executor looks the key up and records
SENT when found, UNKNOWN otherwise (I4). It never posts again on the strength of "it probably did
not happen". Scenario S13a (timeout after writing) resolves; S13b (wrong amount) never does.

## 3. A credit is proven by reading the ledger back

`CreditWritePort.expected_snapshot` is money-rules.md section 8: the transaction exists, is a
POSTED CREDIT, the amount matches to the paisa, shipment, reason and key match, and exactly one
credit exists for that shipment and reason. Anything else is UNKNOWN `verification_mismatch` and
a person decides. A credit that is not proven is never confirmed to the seller.

## 4. Credited and told (ADR 0012)

A verified credit is followed by a `credit_confirmation` message through the normal approval path
(T22); it cites `credited_amount`, a **kernel fact** established from the verified transaction,
which R09 now accepts alongside the assessment's facts (policy-rules.md section 7 rule 1). Only the
verified confirmation resolves the case (T24). A confirmation that cannot be drafted or is denied
escalates: the seller is told by a person.

## 5. Three layers refuse an unapproved credit

The executor's binding check (approved, unused, same intent, same hash, same amount, unexpired);
the domain model (an action of type POST_CREDIT without an approval cannot be constructed); the
schema (`tr_actions_approval_binding`, the CHECK, `ux_actions_one_credit`, the single-use index).
Each is tested on its own, the last with raw SQL.

## 6. The amount is netted, and unknown is refused

The credit amount is `credit_amount(assessment, reason, already credited, already credited for
duplicates)`, where "already credited" sums every live credit action on the shipment (PLANNED,
SENT, UNKNOWN or VERIFIED, any case) from the action store - so a planned credit counts before it
is posted. An action store or ledger that cannot be read refuses (`ledger_state_unknown`); it is
never treated as zero.

## 7. One code path for trial, demo and tests

`api/full_run.py` assembles the whole line once. `make try-agent` stops it at the first approval;
`make demo ARGS="--offline"` answers every approval; the end-to-end tests do either. They cannot
drift apart.

## Deferred (BACKLOG.md)

The long-running runtime (`make run-api`) still assembles only the Phase 1 steps; the live demo
against Outlook and Jira needs real adapter builders and the owner's accounts; the seller-reply
round (reply in, interpreter, re-evaluate) and declared timer effects beyond approval expiry.
