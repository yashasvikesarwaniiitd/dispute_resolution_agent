# 0052 — The hub answers in a Jira comment, and every reply reaches someone

Date: 2026-11-23
Status: **accepted** (owner, 2026-11-23: "Yes build fast", then "Not working" on WD-66). Builds on card P3-07 (commit 3a542f2) and ADR 0020 (Jira idempotency).

## The problem

The agent opens a Jira issue asking the hub to remeasure a parcel. Its description said "record the result in the finding fields", but no Jira project has such fields, so the hub was never told how to answer. When the agent learned to read answers from comments (P3-07), it accepted two exact forms. The owner, acting as the hub on WD-66, wrote "The previous measurement was correct, use that only." That matched neither form, so the comment was ignored and the case kept waiting on the hub, after the hub had answered.

## Decision

1. **The issue says how to answer.** The remeasurement issue's description (kernel, `investigate._payload`) now asks for a comment whose first line is `Remeasured: L x W x H cm, W kg` or `Measurement confirmed`, and says any other reply goes to a person.
2. **Plain confirmations are read.** Beyond the fixed forms, a first line that starts with the usual ways of saying the original measurement stands ("the previous measurement was correct", "measurement is correct", "confirmed") is read as no fault found. A line with any negation ("not", "incorrect", "wrong", "n't") never is.
3. **No reply is dropped.** A comment that is neither becomes a finding of kind `inspected` with no measurement, and its words travel as untrusted text (I5). It can never prove a credit (I9). The timeline shows the words "exactly as recorded". It is passed on only while the case waits on the hub, so a "thanks" under a closed case's issue does not reopen it.

## Why

A person answering in Jira writes a sentence, not a template. Reading the common confirmations covers what people actually write. Passing everything else to a person means the agent never waits for an answer it has already received. Neither path lets free text decide anything: only a parsed `Remeasured:` line carries figures, and only figures reach the calculator.

## Options considered

- **A model reads the comment.** It would understand more phrasings, but a model reading text from outside the system to decide a money question is what I5 forbids. Rejected.
- **Keep two exact forms and only fix the description.** The description helps new issues, but the owner's real reply showed people will not always follow it. Rejected on its own.

## Consequences

- New remeasurement issues tell the hub how to answer. Issues opened before this keep the old text, but their replies are still read or passed on.
- Kernel change: the description string in `kernel/orchestrator/investigate.py`. No rule, transition or invariant changed.
- Tests: `tests/unit/modules/ticketing/test_hub_comment.py` (the owner's own sentence, negations), `tests/unit/modules/ticketing/test_jira_cloud.py` (an unreadable reply becomes `inspected` with its words), `tests/unit/api/test_hub_answer.py`.

## Sources

- Jira Cloud REST API v3, issue comments: https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/
