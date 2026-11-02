# Approvals (Stage A2)

Status: draft for owner approval. Builds on ADR 0013 (the kernel approvals table is the record) and the A1 schema.

Plain-language summary: an approval is a person saying "yes, send exactly this" or "yes, pay exactly this amount". The record is tied to the exact text or amount they saw, so if anything changes afterwards the approval no longer counts. Approvals expire, can be used once, and every one is in the audit trail with who decided and when.

## 1. What needs approval

| Subject | When | Source |
|---------|------|--------|
| Credit | always (I3, R13) | `credits.require_approval = true` (cannot be configured off) |
| Message | when its kind is in `messages.review_required_kinds`, or in neither list (R08) | pilot: every kind (D6), including `clarification_request` |
| Jira issue | never in v1 (internal, no money, no seller contact) | — |

## 2. The approval record

Fields (A1 `Approval` model and `approvals` table, plus the A2 additions marked **new**):

| Field | Meaning |
|-------|---------|
| `id` | `apr_<ULID>` |
| `case_id`, `intent_id` | what it is for; one live (PENDING) approval per intent |
| `subject` | CREDIT or MESSAGE |
| `payload_hash` | `payload_hash(intent.payload)`: for a message, kind + recipient + subject + body + numbers used + thread; for a credit, shipment + amount + reason + fact ids |
| `amount` | credit amount (credits only) |
| `message_kind` | (messages only) |
| `requested_at`, `expires_at` | `expires_at = requested_at + approvals.expire_after_hours` (72) |
| `notified_at` | when the review queue was told |
| `decision` | PENDING, APPROVED, REJECTED, EXPIRED, SUPERSEDED |
| `decided_by` | `human:<id>` of the authenticated approver (section 6) |
| `decided_at` | |
| `note` | reviewer's note; **required (10-500 chars) for REJECTED**, optional for APPROVED |
| **new** `payload_hash_seen` | the hash of what the review screen showed, sent back with the decision; must equal `payload_hash` |
| **new** `presentation_version` | version of the review screen layout that showed it (for audit) |
| **new** `withdrawn_at` | when the queue was told the approval is no longer needed |
| `used_by_action_id` | set once, when the executor consumes it |

## 3. Binding

An approval counts for an action only if every one of these holds at execution (R10, R13, R25, `executor.plan`):

1. `decision = APPROVED`, decided by a human, before `expires_at`.
2. `intent_id` equals the action's intent.
3. `payload_hash` equals `payload_hash(intent.payload)` recomputed now (the stored payload has not been edited).
4. For credits, `amount` equals the intent's amount exactly.
5. `used_by_action_id` is null (single use), and it becomes this action's id in the same transaction.
6. `payload_hash_seen` equals `payload_hash` (the reviewer saw this exact payload).

There is no "edit and approve". A reviewer who wants different wording rejects with a note; the agent re-drafts, which creates a new intent with a new hash and a new approval. This keeps "the text sent is the text approved" (I13) trivially checkable.

## 4. Lifecycle

```
PENDING ──approve──► APPROVED ──consumed by plan──► (used_by_action_id set)
   │
   ├──reject (note required)──► REJECTED
   ├──expiry timer──► EXPIRED          (case escalates: approval_expired)
   └──new evidence / deadline / escalation──► SUPERSEDED   (queue item withdrawn)
```

| Event | Approval | Intent | Case |
|-------|----------|--------|------|
| Approved | APPROVED, then consumed | APPROVED -> PLANNED | T10 -> EXECUTING |
| Rejected, credit | REJECTED | REJECTED | escalate `credit_rejected` (D40) |
| Rejected, message from a proposal, under the cap | REJECTED | REJECTED | T11 -> INVESTIGATING (the note goes into the next briefing's `prior_rejections`) |
| Rejected, reminder intent | REJECTED | REJECTED | T12 -> AWAITING_SELLER (reminder skipped) |
| Rejected, follow-up intent, or over `approvals.max_rejections_per_case` (2) | REJECTED | REJECTED | escalate `follow_up_rejected` / `approval_rejected_limit` |
| Expired (APPROVAL_EXPIRY timer) | EXPIRED | EXPIRED | escalate `approval_expired` |
| Superseded (reply, ops finding, deadline, escalation) | SUPERSEDED, `withdrawn_at` set | SUPERSEDED | per transition |
| Decision arrives after expiry or supersession | unchanged | unchanged | `EVENT_IGNORED(stale_approval)` |
| Decision with a mismatching `payload_hash_seen` | unchanged | unchanged | `EVENT_IGNORED(payload_hash_mismatch)` |

## 5. What the reviewer sees

The review screen (Streamlit, Phase 3 for messages, Phase 5 for credits) shows, in this order, and hashes exactly what it shows into `payload_hash_seen`:

1. **What will happen:** "Send this email to <address>" or "Post a credit of ₹X to shipment <ref>".
2. **The exact payload:** full subject and body (plain text, as it will be sent), or amount, reason code and shipment.
3. **Facts used, from code:** every fact cited, with its value and source evidence id, labelled "calculated by the system".
4. **Policy result:** the verdict and every rule that fired, and every caution signal.
5. **The AI's view, clearly labelled:** the proposal's rationale and findings, labelled "AI recommendation, not verified". Seller text appears only inside a box labelled "Written by the seller (unverified)".
6. Approve / Reject (note required) buttons. No edit field.

Why this order: the approver should anchor on verified facts and the exact consequence, not on the AI's persuasive summary (threat T18).

## 6. Who can approve

- Only an authenticated human. The actor on `APPROVAL_DECIDED` and every `HUMAN_*` event is set by the API from the authenticated session, never read from the request body (threat T09).
- v1 pilot mechanism (A3 `config.md` finalises): the API binds to `127.0.0.1` only; review and console routes require the `REVIEW_CONSOLE_TOKEN` secret (from `.env`, never logged); the approver id comes from `approvals.approver_ids` (an allowlist of human ids, for example `human:owner`). A request with a valid token but an id not on the allowlist is refused.
- Separation of duties: the pilot has one approver (the owner). A credit approved by the same person who reviewed the evidence is accepted in v1 and listed as residual risk RR3.

## 7. Audit

Every approval appears in the case's events: `APPROVAL_REQUESTED` (with the hash and amount), the `APPROVAL_DECIDED` input (approver, decision, note, hash seen), `APPROVAL_SUPERSEDED` when relevant, and the `ACTION_PLANNED` that consumed it. The approvals table keeps the current state; the events keep the history.

## 8. Schema additions (migration in Phase 3)

```sql
ALTER TABLE approvals ADD COLUMN payload_hash_seen TEXT CHECK (payload_hash_seen GLOB 'sha256:*');
ALTER TABLE approvals ADD COLUMN presentation_version TEXT;
ALTER TABLE approvals ADD COLUMN withdrawn_at TEXT CHECK (withdrawn_at GLOB '????-??-??T??:??:??.??????Z');
-- an APPROVED decision must carry the hash the reviewer saw, equal to the bound hash
-- (added as a CHECK in the table rebuild that SQLite needs for new table constraints):
--   CHECK (decision <> 'APPROVED' OR payload_hash_seen = payload_hash)
--   CHECK (decision <> 'REJECTED' OR length(note) >= 10)
```

## 9. Test ideas

- Approve with a stale `payload_hash_seen` (payload re-drafted after the screen loaded): ignored; nothing sent.
- Reject without a note: refused at the API (422).
- An approval used twice: the second `plan` fails (`approval_stale`), and the UNIQUE index refuses it.
- A decision posted with a body claiming `actor: human:owner` but no token: refused (401); with a token and a non-allowlisted id: refused (403).
- Expiry: no decision within 72 h -> EXPIRED, case escalates; a decision after that is ignored.
- A reply arriving while a credit approval is pending supersedes it and withdraws the queue item.
