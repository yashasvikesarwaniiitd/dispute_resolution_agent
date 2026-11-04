# Demo walkthrough

Everything here runs offline, on synthetic data, and costs nothing.

## 1. One dispute, end to end (two minutes)

```bash
make demo ARGS="--offline calc_error_wrong_slab"
```

The seller disputes a charge that was billed on the wrong weight slab. Watch the timeline:

1. **Evidence.** The shipment's status, dimensions, zone and ledger entries are gathered.
2. **Recalculation in code.** The charge is recalculated from the evidence; the error and the
   amount owed are proven by arithmetic, never by the model.
3. **Proposal.** The investigator proposes a credit for that amount and reason.
4. **Your approval.** The credit waits for a person; the demo answers "yes" for you.
5. **The ledger.** The credit is posted with an idempotency key, then read back from the ledger
   and compared to the paisa.
6. **Telling the seller.** A confirmation citing the credited amount waits for approval, is
   sent, and is found in Sent Items with the approved text.
7. **Resolved, credited.**

Other fixtures to try: `kurta_box_larger` (the seller is asked for a photo), `scanner_fault`,
`multi_error` (two proven errors: a person decides), `not_delivered` (ineligible).

## 2. The whole scenario suite

```bash
make eval
```

Plays 57 scenarios - replies, silent sellers, reminders, timeouts, approvals that expire,
ledger failures, injection attempts, the kill switch - and writes `data/eval/suite-report.md`:
every gate with its threshold and measurement, every scenario with its expected and actual
outcome. Each figure carries its sample size.

## 3. The running app

```bash
make run-api
```

The API runs the whole offline line. Post a dispute to `POST /events`; replies, findings and
withdrawals go to `POST /inbound`. A background runner fires timers (reminders, decision
deadlines, approval expiry) every few seconds and recovers in-flight work after a restart.
Decide approvals in the review console:

```bash
make run-approvals
```

## 4. With real accounts (optional)

`make demo` without `--offline` sends a real email and opens a real Jira issue on the owner's
test accounts, and verifies both by reading them back. It refuses to start without them:
`docs/SETUP_CHANNELS.md`.
