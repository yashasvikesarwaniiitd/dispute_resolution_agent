# Events catalog (Stage A1)

Status: draft for owner approval.

Plain-language summary: the `events` table is the case's diary. Every input (an email, a timer, an approval) and every change the system makes (a state change, a proposal, an action) is one line in it, with the reason it happened. Nothing in the diary is ever edited or removed. This document lists every kind of line, what it contains, who writes it and how duplicates are prevented.

## 1. Rules that apply to every event

| Field | Rule |
|-------|------|
| `id` | `evt_<ULID>` from `IdGenerator`. |
| `case_id` | Always set. An inbound event that cannot be routed to a case is never an event; it stays in `inbox` with status `UNROUTABLE` (section 5). |
| `seq` | Assigned by `EventLog.append`: last seq for the case + 1, inside the step's transaction. `UNIQUE(case_id, seq)` makes a race fail loudly instead of reordering. |
| `category` | `INPUT` for the 11 inbound types, `INTERNAL` for everything the kernel writes. |
| `occurred_at` | Input: when it happened at the source (email received time, timer due time, approval decision time). Internal: `Clock.now()` at the step. |
| `recorded_at` | `Clock.now()` at append. |
| `actor` | Input: `EXTERNAL:<source>`, or `HUMAN:<user id>` for approvals and `HUMAN_*` events, or `SYSTEM:scheduler` for timers. Internal: `SYSTEM:system`. |
| `cause` | Input: `INPUT_EVENT` (or `TIMER` / `HUMAN`) with `ref` = its own inbox id. Internal: `STEP` with `ref` = step name, or `RECOVERY` with `ref` = recovery run id. |
| `causation_id` | `NULL` for input events. Internal events: the id of the input event whose handling produced them. During recovery: the latest event of the case at recovery start. |
| `correlation_id` | See section 4. |
| `dedupe_key` | Input events only: `<source>:<source_event_id>` (UNIQUE). Internal events: `NULL`; they are made unique by `(case_id, seq)` and by being written inside the step transaction that caused them, so a retried step that rolled back never left a row. |
| `payload` | The schema for the type below, serialised with `canonical_json`. Payloads never contain secrets (I12). Untrusted text appears only as an `UntrustedText` object, never merged into another string (I5). |
| `schema_version` | 1. A payload change bumps it; readers accept every version they know. |

Ordering guarantee: within a step, events are appended in the order listed in the step tables of `orchestrator.md` section 3. Within a transition, `STATE_CHANGED` comes first, then its effect events (`TIMER_CANCELLED`, `TIMER_SCHEDULED`, `APPROVAL_SUPERSEDED`, `CASE_OUTCOME_RECORDED`) in effect order (transitions.yaml header).

## 2. Input events

Each is first stored in `inbox` (dedupe), then appended to `events` when dispatched to a case. A duplicate submission (same dedupe key) is refused at the inbox and produces no event.

| Type | Source and `source_event_id` | Emitter | Routing to a case | Correlation |
|------|------------------------------|---------|-------------------|-------------|
| `DISPUTE_RAISED` | `dispute_api` or `simulator`, the dispute form id | `POST /events` | intake: open case for (shipment, claim), else a soft-terminal case in window, else a new case (orchestrator.md section 2) | new `corr_<ULID>`; becomes `case.correlation_id` for a new case |
| `SELLER_REPLY_RECEIVED` | `outlook`, the internetMessageId | email poller | `messages.conversation_id` -> case; else sender address + shipment ref in subject if exactly one open case matches; else `UNROUTABLE` | inherits the correlation of the outbound message it answers |
| `DISPUTE_WITHDRAWN` | `seller_portal`, the portal request id | portal API (authenticated, structured) | (shipment, claim) -> open case | new |
| `DELIVERY_FAILURE_RECEIVED` | `outlook`, the NDR internetMessageId | email poller | the failed outbound message id -> its case | inherits the failed message's correlation |
| `OPS_FINDING_RECEIVED` | `jira`, `<issue_key>:<finding_id>` | Jira poller | the issue's idempotency label -> action -> case | inherits the issue action's correlation |
| `APPROVAL_DECIDED` | `approvals`, `<approval_id>:decision` | review queue via `POST /events` | `case_id` in the event | inherits APPROVAL_REQUESTED's correlation |
| `TIMER_FIRED` | `scheduler`, the timer id | `TimerScheduler` | `case_id` in the event | the timer's stored correlation |
| `HUMAN_TOOK_CASE` | `ops_console`, the request id | console via `POST /events` | `case_id` | new |
| `HUMAN_RETURNED_CASE` | `ops_console`, the request id | console | `case_id` | new |
| `HUMAN_REOPENED_CASE` | `ops_console`, the request id | console | `case_id` | new |
| `HUMAN_CONFIRMED_WITHDRAWAL` | `ops_console`, the request id | console | `case_id` | new |

The dedupe key for the approval decision (`approvals:<approval_id>:decision`) means an approval can be decided once; a second click is a duplicate. The timer dedupe key (`scheduler:<timer_id>`) means a timer fires at most once even if two schedulers race.

### 2.1 Input payloads

```python
class DisputeRaised(BaseModel):
    type: Literal["DISPUTE_RAISED"]
    facts: CaseFacts                      # kernel-interfaces.md 4.2; pack_facts validated by the pack

class SellerReplyReceived(BaseModel):
    type: Literal["SELLER_REPLY_RECEIVED"]
    external_message_id: str
    internet_message_id: str
    conversation_id: str | None
    from_addr: str
    subject: UntrustedText
    body: UntrustedText
    attachments: tuple[AttachmentRef, ...]
    received_at: datetime

class DisputeWithdrawn(BaseModel):
    type: Literal["DISPUTE_WITHDRAWN"]
    shipment_ref: str; claim_ref: str; seller_ref: str
    withdrawn_at: datetime
    authenticated_principal: str          # the portal's seller identity; must equal case.facts.seller_ref

class DeliveryFailureReceived(BaseModel):
    type: Literal["DELIVERY_FAILURE_RECEIVED"]
    failed_external_message_id: str
    diagnostic_code: str                  # e.g. "5.1.1"; never the raw NDR body

class OpsFindingReceived(BaseModel):
    type: Literal["OPS_FINDING_RECEIVED"]
    finding: OpsFinding                   # structured measurements + optional UntrustedText note

class ApprovalDecided(BaseModel):
    type: Literal["APPROVAL_DECIDED"]
    approval_id: ApprovalId
    decision: Literal["APPROVED", "REJECTED"]
    payload_hash_seen: PayloadHash        # what the approver was shown; must equal the approval's
    note: str                             # <= 500, staff text

class TimerFired(BaseModel):
    type: Literal["TIMER_FIRED"]
    timer_id: TimerId; kind: TimerKind; due_at: datetime

class HumanTookCase(BaseModel):
    type: Literal["HUMAN_TOOK_CASE"]
    notify_seller: bool
    note: str

class UnknownActionResolution(BaseModel):
    action_id: ActionId
    resolution: Literal["confirmed_done", "confirmed_not_done"]
    evidence_note: str                    # what the human checked (e.g. "found txn T-123 in ledger")

class HumanReturnedCase(BaseModel):
    type: Literal["HUMAN_RETURNED_CASE"]
    reason: str
    unknown_action_resolutions: tuple[UnknownActionResolution, ...]

class HumanReopenedCase(BaseModel):
    type: Literal["HUMAN_REOPENED_CASE"]
    reason: str

class HumanConfirmedWithdrawal(BaseModel):
    type: Literal["HUMAN_CONFIRMED_WITHDRAWAL"]
    note: str
```

Validation at intake (before the inbox row is written; a failure returns HTTP 422 and stores nothing): payload matches its type; `actor.kind == HUMAN` for `APPROVAL_DECIDED` and `HUMAN_*`; `case_id` present where the routing column says so; `DisputeWithdrawn.authenticated_principal` non-empty. Checks that need the case (principal equals seller, payload hash equals approval) happen at dispatch and turn into `EVENT_IGNORED` with a reason, so the attempt is still audited.

## 3. Internal events

All internal events have `category = INTERNAL`, `dedupe_key = NULL`, `actor = SYSTEM:system`. "Emitter" is the kernel component whose step writes it.

| Type | Emitter (step) | When | Payload fields |
|------|----------------|------|----------------|
| `CASE_OPENED` | intake | a new case row is inserted | `shipment_ref, claim_ref, seller_ref, pack, prior_case_id` |
| `STATE_CHANGED` | state machine (any step) | every transition, including T00 | `from_state` (null for T00), `to_state`, `trigger`, `transition_id`, `guards` (names checked), `review_reason` (rendered, when entering NEEDS_HUMAN_REVIEW), `verified_kind` (for action_verified) |
| `ELIGIBILITY_EVALUATED` | eligibility | RECEIVED step | `outcome, reason_codes, pack_version` |
| `EVIDENCE_RECORDED` | gather_evidence | after all requirements are fetched | `gather_round, items: [{evidence_item_id, requirement_key, source, status, error_code}]` (values are in `evidence_items`, not repeated) |
| `ASSESSMENT_RECORDED` | assess | CALCULATING step | `assessment_id, eligible, charged, recalculated, discrepancy, definite_errors, input_hash` |
| `INPUT_SCREENED` | investigate | once per untrusted text screened | `evidence_item_id, kind, flags, blocked, adapter` |
| `PATTERN_CONTEXT_LOADED` | investigate (HOOK A) | every investigate step | `provider, pattern_ids, unavailable: bool` |
| `PROPOSAL_RECORDED` | investigate | the reasoner returned a valid proposal | `proposal_id, action, confidence, reasoner, fallback_used, prompt_name, prompt_version, prompt_hash, model_id, model_route` |
| `REASONER_FALLBACK` | investigate | reasoning raised ModuleUnavailable / TransientPortError and the pack baseline answered | `module, error_code` |
| `INTENT_CREATED` | investigate, reminder, verify, hand_off | an intent is built | `intent_id, source_kind, source_ref, action_type, message_kind, payload_hash, amount, reason_code` |
| `POLICY_EVALUATED` | any step that calls policy; executor.plan | each evaluation | `verdict_id, intent_id, stage, verdict, rule_ids_by_verdict, signal_kinds, review_reason` |
| `APPROVAL_REQUESTED` | investigate, reminder, verify, hand_off | approval row written | `approval_id, intent_id, subject, payload_hash, amount, message_kind, expires_at` |
| `APPROVAL_SUPERSEDED` | transition effect `supersede_pending` | pending approval made obsolete | `approval_id, intent_id, reason` |
| `ACTION_PLANNED` | executor.plan | PLANNED row written | `action_id, intent_id, approval_id, action_type, action_seq, idempotency_key` |
| `ACTION_SENT` | execute | external write confirmed by the adapter | `action_id, external_ref, attempts, duplicate_suppressed, found_by_lookup` |
| `ACTION_FAILED` | execute | known failure, not retried further | `action_id, error_code, retryable, attempts` |
| `ACTION_UNKNOWN` | execute, verify | outcome cannot be confirmed | `action_id, reason` (`ambiguous_after_lookup`, `verification_deadline`, `verification_mismatch`, `recovery_unconfirmed`, `budget_exhausted`, `in_flight_at_escalation`) |
| `VERIFICATION_PENDING` | verify | read-back target not yet visible | `action_id, verify_attempt, next_attempt_at` |
| `ACTION_VERIFIED` | verify | every read-back check passed | `action_id, verified_kind, checks` |
| `UNKNOWN_ACTION_RESOLVED` | dispatch of HUMAN_RETURNED_CASE | a human resolves an UNKNOWN action | `action_id, resolution, evidence_note, actor` |
| `TIMER_SCHEDULED` | transition effect | timer armed | `timer_id, kind, due_at, anchor_at` |
| `TIMER_CANCELLED` | transition effect | timer cancelled | `timer_id, kind, reason` (the transition id) |
| `EVENT_DEFERRED` | dispatch | reaction `defer` | `event_id, state` |
| `EVENT_IGNORED` | dispatch | reaction `ignore`, or a pre-dispatch rule P1-P4 | `event_id, state, reason` (`terminal_state`, `stale_timer`, `stale_approval`, `not_human`, `superseded_case`, `principal_mismatch`, `payload_hash_mismatch`, `not_accepted_in_state`) |
| `REMINDER_SKIPPED` | reminder | reminder intent denied by policy | `intent_id, verdict_id, reason` |
| `NOTICE_NOT_SENT` | hand_off | handed_to_human_notice blocked (T36) | `intent_id, verdict_id, reason` |
| `CASE_OUTCOME_RECORDED` | transition effect `record_outcome` | entering a terminal state | `outcome_id, state, resolved, credited` |
| `MODEL_ROUTE_FAILOVER` | reserved for Phase 4b (ModelGateway failover composite reports it through the reasoner result) | a model call moved from the primary to the secondary route | `purpose, from_route, to_route, reason` (`unavailable`, `rate_limited`, `quota_exceeded`, `credit_window_ended`) |

### 3.1 What is deliberately not an event

- Concurrency retries, step timings and model token counts: these are logs and `model_calls` rows, not audit facts.
- Raw prompt or response text: never stored in events (I14). Proposals keep the structured output; `model_calls` keep counts.
- Outcome delivery to the observer: tracked in `outcome_outbox`, because it must not touch the case.

## 4. Correlation id rules

A correlation id ties together everything caused by one chain of work, across cases and logs.

1. A new external input (`DISPUTE_RAISED`, `DISPUTE_WITHDRAWN`, any `HUMAN_*`) gets a fresh `corr_<ULID>` at intake.
2. An input that answers something we did inherits that thing's correlation: a seller reply or bounce inherits the outbound message action's; an ops finding inherits the issue action's; an approval decision inherits its `APPROVAL_REQUESTED` event's; a timer firing inherits the correlation stored on the timer when it was armed.
3. Internal events carry the correlation of the input being handled.
4. Recovery runs use `corr_<ULID>` created once per recovery run, and set `causation_id` to the case's latest event.
5. Every log line has `case_id`, `event_id` (when one exists) and `correlation_id` (CLAUDE.md section 11). Phase 4b spans use the correlation id as an attribute.

## 5. Unroutable input

An inbound event that cannot be matched to exactly one case (for example a reply to no known conversation, from an unknown address) is stored with inbox status `UNROUTABLE`. It is never guessed onto a case. It appears in the review console's unroutable list. A human can attach it to a case by submitting a new event with the case id (a new dedupe key `ops_console:<request id>`), so the original stays as it was.

## 6. Test ideas (Phase 1)

- Submitting the same `InboundEvent` twice creates one inbox row and one event; the second call returns `duplicate=True`.
- Every transition writes exactly one `STATE_CHANGED` whose `from_state`/`to_state` match the case rows before and after (property test over random event sequences).
- `UPDATE events` and `DELETE FROM events` raise (schema trigger).
- Every internal event of a handled input has `causation_id` = that input's event id and the same `correlation_id`.
- A timer fired twice (two schedulers) produces one `TIMER_FIRED` event.
- An `APPROVAL_DECIDED` whose `payload_hash_seen` differs from the approval's produces `EVENT_IGNORED(payload_hash_mismatch)` and no transition.
- A reply with no matching conversation and no unique sender match stays `UNROUTABLE` and creates no event.
- No event payload contains a string matching the secret patterns used by the I12 log test.
