# Action executor (Stage A1)

Status: draft for owner approval. Interfaces: `kernel-interfaces.md` section 9. Invariants: I2, I3, I4, I10, I13.

Plain-language summary: the executor is the only part of the system allowed to change anything outside it: send an email, create a Jira issue, post a credit. It works like a careful clerk. First it writes down what it is about to do (PLANNED). Then it checks again that it is allowed. Then it looks to see whether it already did it (in case of a crash or retry). Only then does it act (SENT). Finally it goes and reads the result back from the other system to confirm it really happened as intended (VERIFIED). If it cannot tell whether something happened, it says so (UNKNOWN) and a human decides. It never guesses "probably fine".

## 1. Action lifecycle

```
            plan()                execute()                    verify()
 intent ──────────► PLANNED ───────────────► SENT ─────────────────────► VERIFIED
                       │                       │
                       │ known failure          │ read-back mismatch, or
                       ├──────────► FAILED      │ not visible by the deadline
                       │ (never retried after)  └──────────► UNKNOWN
                       │ ambiguous after lookup                 │
                       └──────────► UNKNOWN                     │ human resolution only (T37)
                                       └──► VERIFIED (verified_by human) | FAILED (human_confirmed_not_done)
```

| From | To | Who | Condition |
|------|----|-----|-----------|
| (none) | PLANNED | `plan` | fresh policy verdict allows it; approval bound if required |
| PLANNED | SENT | `execute` | the adapter confirmed the write, or lookup-by-key found it |
| PLANNED | FAILED | `execute` | permanent error, kill switch, approval binding broken, or transient errors exhausted with proof nothing was written |
| PLANNED | UNKNOWN | `execute` | ambiguous outcome and lookup cannot prove either way |
| SENT | VERIFIED | `verify` | every read-back check passes |
| SENT | UNKNOWN | `verify` | a check fails (mismatch), or the target is still not visible at the deadline |
| UNKNOWN | VERIFIED / FAILED | human via `HUMAN_RETURNED_CASE` | the human records what they checked |

The schema trigger `tr_actions_status` enforces exactly this table; `tr_actions_immutable_identity` forbids changing the key, payload hash, intent, type or approval of an existing action.

FAILED is final for that action. A FAILED action never becomes a retry of itself: the case escalates, and if a human returns it, the case re-plans and creates a new intent and a new action (a new `action_seq`, so a new idempotency key). This keeps "one action row = one attempt to do one thing" and makes the audit trail unambiguous.

## 2. Idempotency keys

```python
def idempotency_key(case_id: CaseId, action_type: ActionType, action_seq: int, payload_hash: PayloadHash) -> IdempotencyKey:
    raw = f"{case_id}|{action_type}|{action_seq}|{payload_hash}"
    return IdempotencyKey("wd1:" + hashlib.sha256(raw.encode("utf-8")).hexdigest())   # 68 chars
```

- `action_seq` is `case.action_seq + 1`, taken and stored in the same transaction that writes the PLANNED row (the case save bumps the version, so two planners cannot take the same number; `UNIQUE(case_id, action_seq)` backs it up).
- Why `action_seq` was added to the sketch in `ARCHITECTURE.md` section 9 (`sha256(case_id | action_type | payload_hash)`): without it, two legitimately separate actions with identical payloads (the same evidence request sent again after a reopen) would share a key, and the second would be silently swallowed as a duplicate of the first. With it, a retry of the same action reuses the stored key (it is read from the row, never recomputed), while a new action always gets a new key. ADR 0007.
- The `wd1:` prefix versions the derivation. Changing the formula means a new prefix; stored keys are never recomputed.
- The key travels with every write: as an internet message header and a hidden marker for email, as a Jira label (`wd1-<first 40 hex>` because labels cannot contain `:`; the full key is in an issue property), and as the ledger request's idempotency field. Exact placement per system is Stage A3 (`integrations.md`).

## 3. Execution claim

Before any external call, `execute` takes a lease on the action with one atomic statement:

```sql
UPDATE actions SET claimed_by = :worker, claim_expires_at = :now_plus_lease, attempts = attempts + 1
WHERE id = :id AND status IN ('PLANNED', 'SENT')
  AND (claim_expires_at IS NULL OR claim_expires_at < :now);
```

`attempts` therefore counts execute runs that got past the claim. It is conservative: `attempts == 0` proves no write was ever started for this action, which section 5.1 relies on.

One row updated = claimed. Zero rows = someone else holds it (or it is finished): `execute` returns `blocked(claimed_elsewhere)` without acting. The execute step treats that as **rest** (no commit, no escalation); the runner's stuck-case sweep advances the case again later. The claim is cleared in the transaction that records the outcome. A crashed process's claim simply expires, and recovery takes it.

Lease length: `executor.claim_lease_s` (default 300 s). It must exceed the worst-case duration of one `execute`, which is bounded by a hard budget `executor.execute_budget_s` (default 240 s): the up-front lookup (`lookup_attempts` x `call_timeout_s` + delays 1, 3, 9 s = 43 s), plus `max_attempts` writes (30 s) with backoff (at most 3 x 8 s), plus one ambiguous-outcome lookup with delays (43 s), which is about 140 s. `execute` checks the budget before every external call; if the next call could exceed it, it stops: `unknown(budget_exhausted)` if an ambiguous write happened, else `failed(transient_exhausted)`. Startup refuses `claim_lease_s <= execute_budget_s`.

## 4. `plan(uow, case, intent, ctx)` (inside the step transaction)

1. Re-evaluate policy with `stage = EXECUTION` using fresh counts (contact limits, verified credits for the same shipment and reason) and the current kill switch. Write the `POLICY_EVALUATED` event and verdict row.
2. Accept only:
   - `ALLOW`, or
   - `REQUIRE_APPROVAL` **and** an approval with decision APPROVED, `used_by_action_id` null, `request.intent_id == intent.id`, `request.payload_hash == intent.payload_hash`, and for credits `request.amount == intent.payload.amount` exactly, and `expires_at > now`.
   Anything else raises `PolicyRefused` (the step escalates with `policy_denied`). This is what makes the executor safe to call directly: there is no parameter by which a caller can assert "this is approved" (I2, I13, Phase 1 acceptance).
3. Extra hard checks, independent of the rule set (defence in depth):
   - POST_CREDIT always needs an approval, whatever the verdict says (I3; the schema CHECK backs it).
   - POST_CREDIT is refused if `ActionStore.live_credits(shipment_ref, reason_code)` is non-empty: any POST_CREDIT action for the same shipment and reason, in **any case**, in PLANNED, SENT, UNKNOWN or VERIFIED. Only a FAILED credit (proven not applied, including a human's `confirmed_not_done`) frees the slot. The partial unique index `ux_actions_one_credit` enforces the same rule in the database, so two concurrent planners cannot both pass (I3). `plan` copies `shipment_ref` and `reason_code` into the action's `credit_*` columns for that index.
   - SEND_MESSAGE `to` must equal `case.facts.seller_contact`.
   - Kill switch engaged -> `KillSwitchEngaged` (I10).
4. Compute `action_seq` and the key; insert the PLANNED action; `approvals.consume`; intent -> PLANNED; return the action and the case with `action_seq` incremented (the caller saves the case in the same transaction).

## 5. `execute(action)` (outside any transaction)

```python
def execute(action: Action) -> ExecutionOutcome:
    attempts_before = action.attempts                          # read BEFORE claiming (claim increments it)
    if not claim(action): return ExecutionOutcome(kind="blocked", error_code="claimed_elsewhere", ...)
    if kill_switch.is_engaged():                               # I10, checked here, not only at entry
        return failed(action, "kill_switch_active")
    intent = load_intent(action.intent_id)
    if payload_hash(intent.payload) != action.payload_hash:    # tampering or a bug
        return failed(action, "payload_hash_mismatch")
    if action.approval_id and not approval_still_binds(action):  # approved, consumed by this action, same hash
        return failed(action, "approval_binding_broken")
    port = port_for(action.action_type)
    if port.lookup_supported:                                  # look before you leap, every time
        found = lookup(port, action.idempotency_key)           # bounded retries on TransientPortError
        if found: return sent(action, found, found_by_lookup=True)
    if attempts_before > 0 and not port.idempotent_writes:     # an earlier run may have written (crash, lost commit)
        return unknown(action, "recovery_unconfirmed")         # a lookup miss is not proof: search can lag behind sending
    for attempt in range(1, settings.max_attempts + 1):
        try:
            ref = write(port, intent, action.idempotency_key)  # timeout call_timeout_s
            return sent(action, ref, duplicate_suppressed=ref.duplicate)
        except TransientPortError as e:                        # certainly not applied
            sleep(backoff(attempt, e.retry_after_s)); continue
        except AmbiguousOutcome:
            found = lookup_with_delays(port, action.idempotency_key) if port.lookup_supported else None
            if found: return sent(action, found, found_by_lookup=True)
            if port.idempotent_writes: sleep(backoff(attempt)); continue   # re-send is safe by contract
            return unknown(action, "ambiguous_after_lookup")
        except AuthPortError as e:  return failed(action, "auth", alert=True)
        except PermanentPortError as e: return failed(action, f"permanent:{e.status}")
        except ModuleUnavailable as e: return failed(action, f"module_unavailable:{e.module}")
    return failed(action, "transient_exhausted")               # every attempt was certainly not applied
```

`backoff(attempt, retry_after)` = `max(retry_after or 0, min(cap, base * factor ** (attempt - 1)) * random())` (full jitter; defaults base 1 s, factor 2, cap 8 s). `retry_after` from a 429 is honoured up to the cap; a longer `Retry-After` ends the attempts with `transient_exhausted` rather than blocking the worker.

`lookup_with_delays` tries `lookup_attempts` (3) times with delays 1 s, 3 s, 9 s, because a write that timed out may become visible a moment later.

Only `sent`, `failed` and `unknown` produce an outcome; the orchestrator persists it together with the transition (orchestrator.md section 3.5). If the process dies after the external write but before that commit, the action is still PLANNED; recovery re-runs `execute`, and the lookup finds the write.

### 5.1 Which adapters may be retried

| Adapter flags | Transient error | Ambiguous outcome | Crash recovery |
|---------------|-----------------|-------------------|----------------|
| `lookup_supported` and `idempotent_writes` (simulated ledger) | retry | lookup, else retry | lookup, else retry |
| `lookup_supported` only (expected: Outlook via a key header search; Jira via label search) | retry | lookup with delays; not found -> UNKNOWN | lookup; not found -> write only if `attempts_before == 0`, else UNKNOWN `recovery_unconfirmed` |
| neither | retry | UNKNOWN | write only if `attempts_before == 0`, else UNKNOWN `recovery_unconfirmed` |

Crash recovery runs `execute` again, so its claim makes `attempts >= 1`; the rule therefore reads the value **before** the recovery claim (`attempts == 0` before claiming = no earlier run ever started).

## 6. `verify(action)` (outside any transaction)

Read-back is independent: it reads the external system through the port's read method and compares against what **we** stored (intent payload, case facts, approval), never against what the write call returned.

| Action | Read | Checks (all must pass) | Verified kind |
|--------|------|------------------------|---------------|
| SEND_MESSAGE | `get_message(receipt)` | message exists in Sent Items; `to` is exactly `[case.facts.seller_contact]`; `message_content_hash(snapshot.to, snapshot.subject, snapshot.body) == message_content_hash(intent.payload.to, intent.payload.subject, intent.payload.body)`; idempotency key present; conversation id recorded | the message kind (`evidence_request`, `reminder`, `upheld_explanation`, `credit_confirmation`, `handed_to_human_notice`) |
| UPSERT_ISSUE | `get_issue(ref)` | issue exists in the configured project; carries the idempotency label; summary equals the intent's; description hash equals the intent's; not in a "closed as duplicate" status | `remeasurement_issue` |
| POST_CREDIT | `get_transaction(ref)` and `get_entries(shipment_ref)` | type CREDIT; status POSTED; amount equals the approved amount exactly (`Money` equality); shipment and reason code equal the intent's; idempotency key equal; exactly one CREDIT entry for (shipment, reason) in the ledger | `credit` |

Hash definitions (one each, used everywhere):
- **Approval binding** (I13): `intent.payload_hash = payload_hash(payload)` over the whole payload (for a message: kind, recipient, subject, body, numbers used, thread). The approval stores it; `plan` and `execute` require equality.
- **Read-back content**: `message_content_hash(to, subject, body) = payload_hash({"to": to, "subject": subject, "body": body})`, computed by the executor from the snapshot's plain-text fields and from the stored intent. The adapter returns the body exactly as sent (text/plain); how Graph preserves it is Stage A3.

Result:
- all checks pass -> `verified` (action VERIFIED, `verified_by = system`, `verification_json` lists checks).
- target `ExternalNotFound` or `TransientPortError`, and now < `verify_deadline_at` -> `pending` with `next_attempt_at` from `verify_schedule_s` (0, 5, 30, 120, 300 s); the case rests in VERIFYING.
- any check fails, or the deadline passes -> `failed` (action UNKNOWN with reason `verification_mismatch` or `verification_deadline`); the case escalates with `verification_failed` (I4).

"Verified" for email means "present in our Sent Items, to the right address, with the approved text". It does not mean the seller read it. A later bounce (`DELIVERY_FAILURE_RECEIVED`) escalates the case. ADR 0010.

## 7. Failure modes

| Failure | During | Executor response | Action status | Case |
|---------|--------|-------------------|---------------|------|
| Timeout before the request left (connect timeout, DNS) | write | adapter raises `TransientPortError`; retry with backoff | PLANNED -> SENT on success, else FAILED `transient_exhausted` | VERIFYING, or escalate `action_failed` |
| Timeout after the request left (read timeout) | write | adapter raises `AmbiguousOutcome`; lookup by key with delays | SENT if found; else retry (idempotent adapter) or UNKNOWN | VERIFYING, or escalate `action_unknown` |
| 400 / 422 validation | write | no retry | FAILED `permanent:400` | escalate `action_failed` |
| 401 / 403 | write or read | no retry; ERROR log `auth_failure` with the module name (no token) | FAILED `auth` | escalate `action_failed` |
| 404 on the write target (project or mailbox missing) | write | no retry | FAILED `permanent:404` | escalate `action_failed` |
| 404 on read-back | verify | not visible yet | stays SENT; VERIFY_RETRY | rests in VERIFYING; UNKNOWN at the deadline |
| 409 conflict / duplicate | write | adapter returns the existing ref with `duplicate=True` (or raises `AmbiguousOutcome` and lookup finds it) | SENT, `duplicate_suppressed` | VERIFYING |
| 429 rate limit | write | `TransientPortError(retry_after)`; honour up to the cap | as transient | as transient |
| 500 / 502 / 504 on a write | write | treated as ambiguous (the server may have applied it) | as ambiguous | as ambiguous |
| 503 with no body | write | adapter decides per system (A3); default ambiguous | as ambiguous | as ambiguous |
| Partial: Jira issue created, label update failed | write | lookup by label fails; adapter's composite write raises `AmbiguousOutcome` | UNKNOWN (unless the adapter can find it another way) | escalate `action_unknown` |
| Partial: credit posted, response lost | write | lookup by key finds the transaction | SENT (found by lookup) | VERIFYING |
| Read-back mismatch (wrong amount, recipient or text) | verify | none: this is the alarm the read-back exists for | UNKNOWN `verification_mismatch` | escalate `verification_failed` |
| Kill switch engaged between plan and execute | execute | no external call | FAILED `kill_switch_active` | escalate `kill_switch_active` |
| Module disabled between plan and execute | execute | no external call | FAILED `module_unavailable:<m>` | escalate `module_unavailable:<m>` |
| Process crash after write, before commit | — | recovery: claim, lookup, then section 5.1 | SENT, or UNKNOWN | continues, or escalates |
| Step commit fails after a write, the step escalates instead | — | T90's `mark_in_flight_unknown` turns PLANNED/SENT into UNKNOWN; a human must resolve it before the case returns | UNKNOWN | NEEDS_HUMAN_REVIEW |
| Two workers race on one action | execute | the claim lets exactly one proceed | unaffected | unaffected |

## 8. Settings

`executor.call_timeout_s` 10, `max_attempts` 3, `backoff_base_s` 1, `backoff_factor` 2, `backoff_cap_s` 8, `lookup_attempts` 3, `verify_schedule_s` [0, 5, 30, 120, 300], `verify_deadline_s` 600, `execute_budget_s` 240, `claim_lease_s` 300. A3 `config.md` owns the final schema.

## 9. Test ideas (Phase 1, against an in-memory fake channel)

- Kill switch engaged after plan: `execute` makes zero calls to the fake and returns FAILED `kill_switch_active` (I10).
- Calling `plan` directly for a review-required message with no approval raises `PolicyRefused`; with an approval for a different payload hash, also refused (I13).
- A credit intent with a verdict forced to ALLOW (rule set stubbed) is still refused without an approval (I3 hard check), and the schema refuses the row even if the check is removed (schema test S14).
- Fake raises `AmbiguousOutcome` once and the message is in its store: result SENT with `found_by_lookup`, exactly one message in the fake.
- Same, but nothing in the store and `idempotent_writes = False`: UNKNOWN, and the case escalates; never VERIFIED or resolved (I4).
- Fake with `idempotent_writes = True` and `AmbiguousOutcome` twice then success: one stored write.
- Read-back returns a different recipient: UNKNOWN `verification_mismatch`.
- Read-back 404 three times then found within the deadline: VERIFIED after resting in VERIFYING; at the deadline instead: UNKNOWN.
- Crash simulation: write succeeds, commit is skipped; a new executor's `execute` finds it by lookup and does not write again.
- Crash simulation with a lagging lookup (fake hides the message from `find_sent` for 60 s) and `idempotent_writes = False`: recovery returns UNKNOWN `recovery_unconfirmed` and the fake holds exactly one message.
- Escalation while a credit is PLANNED (step commit fails after the ledger write): the action becomes UNKNOWN; `HUMAN_RETURNED_CASE` without resolving it fails its guard; a second credit for the same shipment and reason from another case is refused by `plan` and by `ux_actions_one_credit`.
- Two threads call `execute` on the same action: the fake sees exactly one write.
- The idempotency key is a pure function of (case, type, seq, payload hash), and two actions differing only in seq get different keys (property test).
