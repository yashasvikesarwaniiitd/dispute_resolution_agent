# Policy rules (Stage A2)

Status: draft for owner approval. Sections 1 and 2 (verdict ordering and the credit proof) were approved at the checkpoint on 2026-11-02 (D28), together with D29 (credit eligibility), D30 (no amount caps) and D31 (low confidence escalates).

Plain-language summary: before the system does anything that touches the outside world (sends an email, opens a ticket, posts a credit), a set of rules each give a verdict, and the strictest verdict wins. Nothing the AI says and no cross-case pattern can make the result less strict. A credit can never get the "just do it" verdict. A separate, smaller set of rules guards every AI call (budget, spend caps, credit window), and one routing rule says exactly when a failed AI call may move to the backup provider.

## 1. Verdict ordering (approved, D28)

| Verdict | Rank | Meaning | What happens |
|---------|------|---------|--------------|
| `ALLOW` | 0 | Nothing objects | Execute (the executor re-checks) |
| `REQUIRE_APPROVAL` | 1 | Allowed only with a stored, bound human approval | Wait for approval; at execution, proceed only with that approval |
| `ESCALATE` | 2 | A person should decide | NEEDS_HUMAN_REVIEW with the rule's reason |
| `DENY` | 3 | This action is forbidden here | NEEDS_HUMAN_REVIEW with the rule's reason (reminder step: skip; hand-off step: notice not sent) |

**Composition law.**

```
verdict = max( ALLOW,
               max(rule.verdict for every rule that ran),
               max(signal.min_verdict for every caution signal) )
review_reason = the reason of the highest-ranked result; ties broken by rule id order (R01 first)
```

- **P1 Monotone in rules:** adding a rule result never lowers the verdict.
- **P2 Monotone in signals:** adding a caution signal never lowers the verdict; `min_verdict` is typed to `{REQUIRE_APPROVAL, ESCALATE}` (I7, I15).
- **P3 No memory:** every evaluation is computed fresh from the current context; no stored verdict is inherited.
- **P4 Fail safe:** a rule that raises becomes `DENY` with reason `rule_error:<id>`. Mandatory rules (R02, R03, R10, R13, R17) missing from the engine stop start-up.
- **P5 Model output is labelled input:** rules read model-produced fields (proposal action and confidence, manipulation flags, reply intent) only to raise a verdict. No rule returns a lower verdict because of anything a model produced.

All rules run on every evaluation (no short-circuit), so the stored verdict lists every objection, which is what an approver and an auditor need.

## 2. Proof sketch: no credit is ever allowed without an approval record (approved, D28)

**Claim A (policy).** For any intent with `action_type = POST_CREDIT`, every evaluation, at any stage, after any history of verdicts and with any signals, returns at least `REQUIRE_APPROVAL`.

1. R13 is mandatory (start-up check; the mandatory set is R02, R03, R10, R13, R17).
2. R13 returns `REQUIRE_APPROVAL` at PLANNING; at EXECUTION `REQUIRE_APPROVAL` if a valid bound approval exists, else `DENY`. Never `ALLOW`. `credits.require_approval` is typed `Literal[True]`.
3. By the composition law, `verdict >= R13 >= REQUIRE_APPROVAL`. By P3 history cannot change this; by P2 signals only raise it.
4. A credit cannot be disguised: `CreditPayload` if and only if `action_type == POST_CREDIT` (pydantic validator and the `action_intents` CHECK). ∎

**Claim B (execution).** No credit is written without a stored, human-decided, bound, unexpired, unused approval, even if Claim A's code had a bug. Five independent layers, any one sufficient:

| Layer | Mechanism | Holds even if |
|-------|-----------|---------------|
| L1 | `executor.plan` requires the bound approval for POST_CREDIT whatever the verdict | the engine returned ALLOW by a bug |
| L2 | A credit reaches EXECUTING only via T10 (`approval_matches_pending`); every other entry has `intent_is_not_credit` (check C7) | `plan` were called on the wrong path |
| L3 | Schema: `approval_id` NOT NULL for credits; trigger `tr_actions_approval_binding` requires the linked approval to be a human-APPROVED, unused CREDIT approval for the same intent, hash and amount; single-use approvals; `ux_actions_one_credit` | the Python checks were removed (schema tests S14, S28, S29) |
| L4 | Approvals are only ever decided by an authenticated human (`decided_by GLOB 'human:*'`; actor bound from the session, section 6 of `approvals.md`) | someone forged a decision through the pipeline |
| L5 | The AI has no write path (I1) | a model tried to approve its own proposal |

The model can request a credit and name a reason; it cannot set the amount (R15), make a reason valid (R14: only reasons code proved, D29) or approve (L4, L5). The stage's stop-and-ask condition (model output influencing a financial write) does not trigger.

## 3. What a rule sees

Rules are pure functions of `PolicyContext` (`kernel-interfaces.md` section 7). Stage A2 adds these fields (applied to `kernel-interfaces.md` section 13):

| Field | Meaning |
|-------|---------|
| `reply_in_round: bool` | this gather round contains at least one seller reply |
| `clarifications_sent: int` | `clarification_request` actions in SENT, VERIFIED or UNKNOWN for the case |
| `draft_blocked_reason: str \| None` | the drafter's `blocked_reason` when it refused to draft |
| `message_tokens: tuple[NumberToken, ...]` | numbers found in the drafted subject and body by `extract_numbers` (section 7) |
| `kernel_facts: tuple[Fact, ...]` | facts the kernel provides for messages: identifier facts (`case_ref`, `shipment_ref`, `claim_ref`), `reply_due_at` (the SELLER_DECISION due date), `credited_amount` (from the verified ledger transaction), and the pack's template constants |
| `prior_case_outcome: CaseState \| None` | outcome of `case.prior_case_id`, if any |
| `outstanding_charge: Money \| None` | charged amount for the shipment minus verified credits (from the ledger read) |

Intents a rule applies to are written as `MSG(kind)`, `ISSUE`, `CREDIT`, or `NONE` (a proposal with no side effect: wait, escalate). Stage `P` = PLANNING, `E` = EXECUTION.

## 4. Action rules

Each entry: **Applies** (intents, stages) · **Fires when** · **Verdict, reason code** · **Config** · **Invariant** · **Negative tests** (inputs that must make the rule fire; test names in `test-charter.md`).

### R01 action_on_menu
- **Applies:** every proposal, P.
- **Fires when:** `proposal.action` is not in the briefing's `allowed_actions` (pack menu minus the kernel credit filter).
- **Verdict:** DENY `action_not_allowed`. (The orchestrator already treats this as `invalid_model_output`; R01 is the policy-side record.)
- **Config:** none. **Invariant:** I8.
- **Negative tests:** `propose_credit` on a case with a verified credit; `uphold_charge` when the pack menu excludes it.

### R02 kill_switch (mandatory)
- **Applies:** MSG, ISSUE, CREDIT; P and E.
- **Fires when:** `ctx.kill_switch` is true.
- **Verdict:** DENY `kill_switch_active`.
- **Config:** `KILL_SWITCH`, `control_flags.kill_switch`. **Invariant:** I10.
- **Negative tests:** switch engaged at P; engaged between P and E; flag unreadable (KillSwitch returns true).

### R03 module_available (mandatory)
- **Applies:** MSG -> `email` (and `approvals` if R08 would require approval); ISSUE -> `ticketing`; CREDIT -> `ledger` and `approvals`; P and E.
- **Fires when:** a needed module is disabled.
- **Verdict:** DENY `module_unavailable:<first missing module>`.
- **Config:** `config/modules.yaml`. **Invariant:** I6.
- **Negative tests:** email off + evidence request; approvals off + any review-required kind; ledger off + credit; approvals off + credit.

### R04 contact_limit_case
- **Applies:** MSG except the outcome messages `upheld_explanation`, `credit_confirmation` and `handed_to_human_notice` (exempt, since blocking them would strand a case at its outcome; they still count towards the totals); P and E.
- **Fires when:** `messages_sent_total >= contact.max_messages_per_case`.
- **Verdict:** DENY `contact_limit_case`. **Config:** `contact.max_messages_per_case` (3). **Invariant:** I13.
- **Negative tests:** fourth evidence request; UNKNOWN sends count towards the limit; positive control: evidence request + reminder + clarification, then `upheld_explanation` is not blocked.

### R05 contact_limit_daily
- **Applies:** as R04, and also exempting `clarification_request` (it answers the seller's own reply, so it is not unsolicited contact; R04's per-case total still bounds it).
- **Fires when:** `messages_sent_today >= contact.max_messages_per_day` (day boundary in `contact.day_boundary_tz`).
- **Verdict:** DENY `contact_limit_daily` (the reminder step turns this into REMINDER_SKIPPED).
- **Config:** `contact.max_messages_per_day` (1), `contact.day_boundary_tz` (Asia/Kolkata). **Invariant:** I13.
- **Negative tests:** a second evidence request on the same IST day; a send at 23:59 and one at 00:01 IST are different days; positive control: a clarification on the same day as the evidence request is not blocked (D19).

### R06 recipient_allowlisted
- **Applies:** MSG; P and E.
- **Fires when:** `intent.payload.to` is not in `messages.allowed_recipients`.
- **Verdict:** DENY `recipient_not_allowlisted`. **Config:** `messages.allowed_recipients` (from `TEST_SELLER_EMAIL` and any extra test addresses). **Invariant:** I11.
- **Negative tests:** a dispute whose seller contact is a non-test address (intake also refuses it); allowlist empty.

### R07 recipient_is_case_seller
- **Applies:** MSG; P and E.
- **Fires when:** `intent.payload.to != case.facts.seller_contact` (case-insensitive comparison of the normalised address).
- **Verdict:** DENY `recipient_mismatch`. **Invariant:** I5.
- **Negative tests:** a reply asking to "send future mail to other@example.test"; a drafter output that names a different address (the payload builder ignores it; the test forces it).

### R08 message_review_required
- **Applies:** MSG; P.
- **Fires when:** `kind in messages.review_required_kinds`, or the kind is in neither list (fail safe).
- **Verdict:** REQUIRE_APPROVAL `message_review_required`. **Config:** `messages.review_required_kinds`, `messages.auto_send_kinds` (pilot: all kinds reviewed, D6). **Invariant:** I13.
- **Negative tests:** every kind with the pilot config; a kind removed from both lists still requires approval.

### R09 message_numbers_verified
- **Applies:** MSG; P and E.
- **Fires when:** any number token in the subject or body is not justified (section 7).
- **Verdict:** DENY `numbers_unverified`. **Invariant:** I9.
- **Negative tests:** "Rs 480" with no fact of that value; a correct number cited with the wrong fact id; a fact id that does not exist; "1.98 kg" written as "2 kg"; numbers written as words are out of scope (residual risk RR1).

### R10 message_approval_bound (mandatory)
- **Applies:** MSG of a review-required kind; E.
- **Fires when:** there is no approval that is APPROVED, unexpired, unused, for this intent, with `payload_hash` equal to the intent's.
- **Verdict:** DENY `approval_missing_or_unbound`. **Invariant:** I13.
- **Negative tests:** no approval; approval for a different intent; payload edited after approval; approval expired; approval already used.

### R11 draft_not_blocked
- **Applies:** NONE or MSG; P.
- **Fires when:** `draft_blocked_reason` is set.
- **Verdict:** ESCALATE `draft_blocked`. **Invariant:** I4.
- **Negative tests:** drafter returns a blocked reason.

### R12 clarification_limit
- **Applies:** MSG(`clarification_request`); P and E.
- **Fires when:** `clarifications_sent >= contact.max_clarifications_per_case`.
- **Verdict:** DENY `reply_unresolved`. **Config:** `contact.max_clarifications_per_case` (1). **Invariant:** I4 (owner decision D19).
- **Negative tests:** a second unclear reply after one clarification.

### R13 credit_requires_approval (mandatory)
- **Applies:** CREDIT; P and E.
- **Fires when:** always.
- **Verdict:** P: REQUIRE_APPROVAL `credit_requires_approval`. E: REQUIRE_APPROVAL if `approval` is APPROVED by a human, for this intent, `payload_hash` equal, `amount` equal, unexpired, unused; otherwise DENY `approval_missing_or_unbound`.
- **Config:** `credits.require_approval` (Literal True). **Invariant:** I3.
- **Negative tests:** every combination of missing, expired, used, wrong-intent, wrong-hash and wrong-amount approvals at E; the hypothesis test in section 2.

### R14 credit_reason_proven
- **Applies:** CREDIT; P and E.
- **Fires when:** `reason_code` is not in `credits.reason_codes`, or not in `assessment.definite_errors`, or the assessment lists more than one definite error (one credit per case, `money-rules.md` section 4).
- **Verdict:** DENY `credit_reason_unproven`; ESCALATE `multiple_definite_errors` for the last case.
- **Config:** `credits.reason_codes`. **Invariant:** I3, I9. **Owner decision:** D29.
- **Negative tests:** model proposes `calculation_error` when the assessment shows none; an unknown reason code; two definite errors.

### R15 credit_amount_deterministic
- **Applies:** CREDIT; P and E.
- **Fires when:** `intent.payload.amount != credit_amount(assessment, reason_code)` (`money-rules.md` section 3), or the amount is not positive, or the cited `fact_ids` do not produce it.
- **Verdict:** DENY `credit_amount_mismatch`. **Invariant:** I3, I9.
- **Negative tests:** amount off by one paisa; amount taken from proposal text; zero amount.

### R16 credit_within_outstanding (structural limit)
- **Applies:** CREDIT; P and E.
- **Fires when:** `outstanding_charge` is unknown, or `amount > outstanding_charge`, where `outstanding_charge` = DEBITs on the charge line minus every CREDIT on it that is not FAILED (PLANNED, SENT, UNKNOWN or VERIFIED, in any case).
- **Verdict:** DENY `credit_exceeds_outstanding` (unknown -> ESCALATE `ledger_state_unknown`).
- **Config:** none. Owner decision D30: no amount caps; the structural limit is what makes "an amount within limits" (I3) true. **Invariant:** I3.
- **Negative tests:** credit larger than the remaining charge after an earlier credit for another reason; ledger read fails.

### R17 credit_not_duplicate (mandatory)
- **Applies:** CREDIT; P and E.
- **Fires when:** `live_credits_same_shipment_reason > 0` (any case; PLANNED, SENT, UNKNOWN or VERIFIED).
- **Verdict:** DENY `duplicate_credit`. **Invariant:** I3. Backed by `ux_actions_one_credit`.
- **Negative tests:** second case for the same shipment and reason; an UNKNOWN earlier credit; a FAILED earlier credit does **not** fire (positive control).

### R18 no_second_outcome_after_credit
- **Applies:** CREDIT and MSG(`upheld_explanation`); P.
- **Fires when:** `case_has_verified_credit`.
- **Verdict:** DENY `credit_already_posted`. **Invariant:** I3 (and ADR 0012).
- **Negative tests:** reopened credited case proposes uphold; proposes a credit for another reason.

### R19 wait_needs_expectation
- **Applies:** NONE(`wait`); P.
- **Fires when:** no active OPS_TIMEOUT; or `reply_in_round` (D19), unless OPS_TIMEOUT is active and every reply reading in the round has intent `irrelevant` or `disputes_again_no_new_info` (a seller acknowledgement while operations is still remeasuring is not a reason to escalate).
- **Verdict:** DENY `reply_unresolved` (after a reply) or `wait_without_expectation`. **Invariant:** D19, I4.
- **Negative tests:** wait after an unclear reply with no ops wait open; wait after a human return (no timers); positive control: an ops finding of `inspected` plus a seller "thanks, waiting" reply while OPS_TIMEOUT is active -> T09.

### R20 withdrawal_claimed
- **Applies:** any intent or NONE; P.
- **Fires when:** a reply reading in this round has `intent == withdraws_dispute`.
- **Verdict:** ESCALATE `seller_withdrawal_claimed`. **Invariant:** I5. **Owner decision:** D20.
- **Negative tests:** "please close my dispute" email; withdrawal hidden in a long reply.

### R21 manipulation_flagged
- **Applies:** any intent or NONE; P.
- **Fires when:** `proposal.manipulation_flags` is non-empty, or any reply reading has `intent == contains_instruction_attempt` or manipulation flags, or any screen result has an `instruction_like` or `prompt_shield:*` flag.
- **Verdict:** ESCALATE `manipulation_suspected`. **Invariant:** I5, I15.
- **Negative tests:** the injection corpus (test charter section 5); a clean result produces no signal and changes nothing (positive control).

### R22 low_confidence_consequential
- **Applies:** proposals `propose_credit`, `uphold_charge`; P.
- **Fires when:** `proposal.confidence == low`.
- **Verdict:** ESCALATE `low_confidence`. **Invariant:** I4. **Owner decision:** D31.
- **Negative tests:** low-confidence uphold; medium does not fire (positive control).

### R23 guardrail_blocked
- **Applies:** any; P.
- **Fires when:** any screen result in the round has `blocked = true`.
- **Verdict:** ESCALATE `guardrail_blocked` (no failover, F01). **Invariant:** I15.
- **Negative tests:** blocked reply; blocked ops note.

### R24 repeat_dispute (signal)
- **Applies:** cases with `prior_case_id`; P.
- **Produces:** a `CautionSignal(kind=repeat_dispute, min_verdict=REQUIRE_APPROVAL)`, not a rule result. If the shipment has any non-FAILED credit (in any case, whatever that case's outcome), the signal is ESCALATE.
- **Invariant:** I7. **Negative tests:** repeat after a credited case escalates; signal never lowers (P2 property).

### R25 approval_fresh
- **Applies:** any intent with an approval; E.
- **Fires when:** the approval's decision is EXPIRED or SUPERSEDED, `withdrawn_at` is set, `expires_at <= now`, or it is already used.
- **Verdict:** DENY `approval_stale`. **Invariant:** I3, I13.
- **Negative tests:** approval decided after its expiry timer fired (the decision event is ignored; this rule is the second line).

### Signals that are not rules

- Screen flags (I15): each flagged screen result -> `screen_flag` signal, min REQUIRE_APPROVAL. Screen unavailable -> `screen_flag` reason `screen_unavailable`.
- Pattern matches (Phase 7, I7): `pattern_match`, min `patterns.match_min_verdict`; provider failure -> `pattern_context_unavailable`, min REQUIRE_APPROVAL; `pattern_influence.changed_recommendation` -> `pattern_changed_recommendation`, min REQUIRE_APPROVAL.
- Sender not authenticated (SPF/DKIM/DMARC fail reported by the mail adapter, A3): `sender_unauthenticated`, min ESCALATE (threat T03).

## 5. Model-call rules (`kernel/policy/model_calls.py`)

Checked before every model call by `BudgetedModelGateway`, a kernel decorator around the configured `ModelGateway` (I14). They raise `BudgetExceeded(kind)`, which the orchestrator maps to the review reason. Spend comes from the new port `ModelUsageLedger` (reads `model_calls`).

| Id | Rule | Fires when | Result | Config |
|----|------|------------|--------|--------|
| M01 | calls per case | `calls_for_case >= llm.max_calls_per_case` | `llm_budget_exceeded` | `llm.max_calls_per_case` (6) |
| M02 | output tokens | `req.max_output_tokens > llm.max_output_tokens` | `DomainValidationError` at build time (a bug, not a budget) | `llm.max_output_tokens` (1200) |
| M03 | primary spend cap | primary route and `spent_usd(primary) + estimate > llm_primary.spend_cap_usd` | `credit_cap_reached` (no failover, D17) | `llm_primary.spend_cap_usd` (150) |
| M04 | credit window | primary route, `today > llm_primary.credit_expires_on` and not `allow_paid_usage` | raises `TransientPortError(reason=credit_window_ended)` so F01 can route to the secondary; with no secondary available the case escalates `credit_window_ended` | `llm_primary.credit_expires_on`, `allow_paid_usage` (false) |
| M05 | secondary spend cap | secondary route and `spent_usd(secondary) + estimate > llm_secondary.spend_cap_usd` | `credit_cap_reached` (no further route) | `llm_secondary.spend_cap_usd` (10) |
| M06 | secondary is Haiku only | secondary model id does not match `llm_secondary.allowed_models` | `ConfigError` at start-up | `llm_secondary.allowed_models` (`claude-haiku-*`) |

`estimate` = `max_output_tokens` priced at the route's output rate plus the rendered prompt's input tokens at the input rate (`config/pricing.yaml`), so a cap is never exceeded by more than zero calls.

## 6. Failover rule F01

Failover moves one call from the primary route (Foundry) to the secondary (Anthropic API, Haiku). It changes only the provider: prompt, schema, validation, policy and approvals are identical.

| Cause on the primary | Failover? | Otherwise |
|----------------------|-----------|-----------|
| `TransientPortError(reason=unavailable)` after the route's retries | yes | — |
| `TransientPortError(reason=rate_limited)` after retries | yes | — |
| `TransientPortError(reason=quota_exceeded)` | yes | — |
| M04 credit window ended | yes | — |
| M03 primary spend cap reached | **no** (D17: never fail over past a cap) | escalate `credit_cap_reached` |
| `GuardrailBlocked` | **no** | escalate `guardrail_blocked` |
| `AgentDrift` | **no** | escalate `agent_drift` |
| `InvalidModelOutput` (after the one retry) | **no** | escalate `invalid_model_output` |
| M01 calls per case | **no** | escalate `llm_budget_exceeded` |
| M05 secondary cap, or the secondary also fails | **no further route** | rules-only baseline decides clear cases; others escalate (FLOW 7b) |
| `AuthPortError`, `PermanentPortError` | **no** (a configuration fault must be seen, not hidden) | escalate `module_unavailable:model_gateway` and log ERROR |

Each failover writes `MODEL_ROUTE_FAILOVER` with the reason and route. Failover is allowed only if the secondary is enabled and M05/M06 pass. Why the credit window may fail over but a spend cap may not (D17): the window ending is an availability event the owner planned for (the free credit simply stops), whereas a cap is a spending limit the owner set on purpose, and silently spending on another provider would defeat it.

## 7. Number validation (R09)

`extract_numbers(text)` finds every token matching `(₹|Rs\.?|INR)?\s?\d[\d,]*(\.\d+)?\s?(kg|g|cm|%)?` (case-insensitive), with its unit if any. Each token is justified if, and only if, one of:

1. a `numbers_used` entry has `text` equal to the token (after trimming), its `fact_id` exists in `assessment.facts` or `kernel_facts`, and the fact's value rendered by `render_fact` equals the token's normalised value (Money compared as paise, Weight as grams, Decimal exactly; units must agree; "₹1,234.50", "Rs 1234.50" and "INR 1234.5" are equal, "1234" is not equal to "1234.50" for money);
2. it lies inside an identifier rendered from an **identifier fact**: `shipment_ref`, `claim_ref` or the display case reference `case_ref` (for example `CASE-1042`); identifiers are matched as whole strings before tokenising;
3. it lies inside a date or time rendered by `render_fact` from a kernel date fact (`reply_due_at`), always in the fixed formats `5 November 2026` and `17:00 IST`; these rendered strings are masked as whole strings before tokenising, so their parts are never separate tokens;
4. it is a **template constant** fact declared by the pack (for example `reply_window_hours = 72`), cited in `numbers_used` like any other fact.

For `credit_confirmation`, the credited amount is the kernel fact `credited_amount`, taken from the verified ledger transaction (not from the assessment).

Anything else fails R09. Words for numbers ("four hundred") are not detected: residual risk RR1, mitigated by human review of every message in the pilot (D6).

## 8. Review reasons

Every ESCALATE or DENY verdict carries a `ReviewReason`. The rule's reason maps to a `ReviewCode` (the enum in `kernel-interfaces.md` section 3, extended in section 13). Reasons without a dedicated code use `policy_denied` / `policy_escalated` with the reason in `detail`.

| Rule reason | ReviewCode | detail |
|-------------|------------|--------|
| `kill_switch_active` (R02) | `kill_switch_active` | |
| `module_unavailable:<m>` (R03) | `module_unavailable` | module = `<m>` |
| `credit_rejected` (apply_approval step, D40) | `credit_rejected` (new in A4) | |
| `reply_unresolved` (R12, R19), `wait_without_expectation` (R19), `seller_withdrawal_claimed` (R20), `guardrail_blocked` (R23) | the code of the same name | |
| `manipulation_suspected` (R21), `low_confidence` (R22), `multiple_definite_errors` (R14), `ledger_state_unknown` (R16) | the code of the same name (new in A2) | |
| `sender_unauthenticated` (signal at ESCALATE) | `sender_unauthenticated` (new) | |
| `repeat_dispute` (R24 at ESCALATE) | `policy_escalated` | `repeat_dispute` |
| `draft_blocked` (R11) | `policy_escalated` | `draft_blocked` (as in orchestrator.md section 3.6) |
| every other DENY reason (`action_not_allowed`, `contact_limit_*`, `recipient_*`, `numbers_unverified`, `approval_missing_or_unbound`, `approval_stale`, `credit_*`, `duplicate_credit`, `rule_error:<id>`) | `policy_denied` | the reason |
| M01 | `llm_budget_exceeded` | |
| M03, M05 | `credit_cap_reached` | the route |
| M04 with no secondary | `credit_window_ended` (new) | |

When several rules fire, the case's review reason is the highest-ranked one; the stored verdict keeps them all.

## 9. Rule ids are stable

A rule id is never reused. Changing what a rule does changes its id suffix (R09 -> R09b) and adds an ADR line. `rules_version` (stored on every verdict) is `sha256` of the sorted rule ids plus the canonical JSON of `PolicyConfig`, so any verdict can be replayed against the exact rules that produced it.
