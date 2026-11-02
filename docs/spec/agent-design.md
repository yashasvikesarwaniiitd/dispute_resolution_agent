# Agent design (Stage A4)

Status: draft for owner approval. What the AI sees, what it may answer, what happens when it fails, the rules-only baseline it is measured against, and the two-pass design for Phase 7. Models: `kernel-interfaces.md` section 4.7; prompts: `prompts/`; review: `prompt-review.md`.

Plain-language summary: the AI sees a carefully built summary of one case, never the raw database, and it can answer only with one of a few allowed next steps. Facts calculated by code are labelled as verified; anything a seller wrote is fenced off and labelled as untrusted. If the AI misbehaves, runs out of budget or is blocked, the case goes to a person. A simple rules-only version handles the clear-cut cases with no AI at all, and every AI result is compared against it.

## 1. The briefing (`CaseBriefing` -> template context `b`)

The briefing is built by code (`orchestrator/briefing.py`) and rendered by `user.md.j2`. The fields the template uses, with their caps:

| Template field | From | Trust | Cap and truncation |
|----------------|------|-------|--------------------|
| `b.case_ref` | display reference `CASE-<n>` | system | — |
| `b.facts.shipment_ref`, `claim_ref`, `seller_ref`, `raised_at` | `CaseFacts` | system | identifiers ≤ 64 chars |
| `b.assessment_view` | `ChargeAssessment`: `eligible`, `charged`, `recalculated`, `recalculated_remeasured`, `discrepancy`, `definite_errors`, and `facts` (`fact_id`, `label`, rendered value) | system | ≤ 40 facts; each rendered value ≤ 100 chars (4,000) |
| `b.trusted_evidence[]` | evidence items with provenance SYSTEM or OPS structured fields: `id`, `source`, `status`, `value` (JSON) | system | ≤ 12 items; each value ≤ 700 chars of JSON, else replaced by `{"truncated": true, "keys": [...]}` (8,400) |
| `b.untrusted_evidence[]` | every `UntrustedText` in the round (dispute statement, seller replies, ops notes, shipment notes) **and the structured `value` of every SELLER-provenance evidence item**: `id`, `source`, `text` | outside party | ≤ 5 items; each ≤ 1,000 chars (head 750 + tail 250 with a `[... n characters omitted ...]` marker); the full text stays in the database (5,000) |
| `b.reply_reading` | latest `ReplyReading` (AI-derived) | outside party | its schema caps (summary ≤ 300); ≤ 600 rendered |
| `b.contact` | `reply_in_round`, `clarifications_sent`, `remeasurement_outstanding` (OPS_TIMEOUT active) — from `PolicyContext`, carried on the briefing by amendment 15.1 | system | — |
| `b.screen_results[]` | `CaseBriefing.screen_results`: `source`, `flag` (flag names only, never the flagged text) | system | ≤ 10; each ≤ 60 chars (600) |
| `b.related_cases[]` | `CaseRepository.related`: `case_ref`, `state`, `closed_at` | system | ≤ 10, newest first; outcome only, no seller text; each ≤ 80 chars (800) |
| `b.pattern_context[]` | Phase 7 patterns: `id`, `status`, `summary` | **outside party** when `summary` was produced by the `pattern_analyst` prompt (it is derived from other cases' text); system when produced by code | ≤ 5; summary ≤ 400 (2,000). Model-produced summaries render inside `<untrusted_pattern_context>` |
| `b.prior_rejections[]` | approval rejection notes in this round | staff | ≤ 3; each ≤ 400 (1,200) |
| `b.allowed_actions[]` | section 2 | system | non-empty |

Total rendered user message ≤ 24,000 characters. The caps above sum to 23,000 in the worst case, leaving 1,000 for block markup, so a briefing within its per-field caps always fits. If a briefing would still exceed the total, the shrink order is: (1) shorten untrusted texts further, oldest first, to a floor of 300 chars each; (2) drop related cases, oldest first; (3) drop pattern context, lowest `status` confidence first; (4) replace trusted evidence values with their `{"truncated": true, "keys": [...]}` form, largest first. Only if the assessment view and the remaining trusted facts alone still exceed the budget does the case escalate `internal_error` (a pack bug). More than 5 untrusted texts: the dispute statement is always kept, then the newest replies fill the remaining slots, so the required input of section 1 is never silently dropped. The `briefing_hash` is computed over the rendered message, so a proposal can be reproduced exactly; in the two-pass design (section 7) each pass renders its own message and `Proposal.briefing_hash` stores the **pass-2** hash, with P1's hash recorded on the `MODEL_CALL` row for pass 1.

Reply-interpreter context (`ctx` in its template): `case_ref`, `request_sent_at`, `requested_items` (from the last verified evidence request or clarification), `reply_text` (≤ 20,000 chars — `UntrustedText.text`'s own cap), `reply_truncated` (`UntrustedText.truncated`, rendered as an explicit marker so the model knows it did not see the whole reply), `attachments` (name, content type). Drafter context: `kind`, `case_ref`, `facts` (approved facts with `kind` and `rendered`), `requested_items`, `open_points` (clarification only: the `missing`/`partial` requested items and the proposal's missing- and conflicting-evidence statements), `next_step`, `style` (tone `polite`, language from the seller's last reply or `en`, max words 180). Both shapes are added to `kernel-interfaces.md` by amendment 15.1; A4 introduces no field that the approved data model does not carry.

## 2. Allowed actions (the menu)

The reasoner runs only in INVESTIGATING. The menu is the pack's `allowed_actions(state, assessment)` intersected with the kernel filters:

| Situation | Menu |
|-----------|------|
| Eligible, assessment complete, no credit on the claim line | all six actions |
| `definite_errors` empty | keeps `propose_credit` (R14 denies it with `credit_reason_unproven`); the menu is not used to hide reasons |
| The shipment has a live or verified credit (kernel filter) | drops `propose_credit` and `uphold_charge` (the kernel menu rule in `orchestrator.md` section 3.6; R17 covers the live-credit case and R18 the verified one) |
| `clarifications_sent >= 1` and `reply_in_round` | keeps `request_seller_evidence` (R12 will deny it, giving the `reply_unresolved` escalation with its reason) |
| No OPS_TIMEOUT active | keeps `wait` (R19 denies it with a reason); the menu is not used to hide reasons |
| Always | contains `escalate_to_human` |

Why the menu does not remove everything policy would deny: a denied proposal produces an explicit, recorded reason (`reply_unresolved`, `wait_without_expectation`), which is better for audit and evals than a silently narrower menu. The menu removes only options that could be dangerous to even propose (credits and upholds once money has moved).

## 3. Proposal

Schema: `prompts/case_investigator/v1/schema.json`, mirrored by `Proposal` (`kernel-interfaces.md` section 4.7). Checks after schema validation, any failure = `InvalidModelOutput`:
1. `action` is in the menu.
2. Every `evidence_refs` entry is an id present in the briefing (fact ids or evidence ids).
3. `propose_credit` has a `reason_code`; `request_seller_evidence` has 1-3 `requested_items`; `request_remeasurement` has a `remeasurement_question` (≤ 400 chars).
4. `pattern_influence.considered` is false when the briefing had no patterns.

Checks 2 and 3 are the mitigation for threat T12 (fabricated evidence ids and unsupported parameters); `threat-model.md` and `traceability.md` point here. `b.related_cases[]` renders `case_ref` rather than `RelatedCase.case_id`, and omits `discrepancy`, so no amount from another case reaches the model.

## 4. Failure handling

| Failure | Result | Failover (F01)? |
|---------|--------|-----------------|
| Invalid JSON or schema, or a check in section 3 fails | one identical retry; then escalate `invalid_model_output` | no |
| Output cut short (`max_output_tokens`) or Anthropic `refusal` | as invalid | no |
| Per-case calls exhausted (M01) | escalate `llm_budget_exceeded` | no |
| Spend cap reached on either route (M03 primary, M05 secondary) | escalate `credit_cap_reached` | no (D17: never fail over past a cap; `policy-rules.md` section 6 amendment) |
| Guardrail block | escalate `guardrail_blocked` | no |
| Agent drift | escalate `agent_drift` | no |
| Provider unavailable, rate-limited, quota, credit window | Haiku secondary | yes |
| Both routes unavailable, or `reasoning`/`model_gateway` disabled | rules-only baseline (section 5) with `fallback_used = true`; it escalates `module_unavailable:<module>` when it cannot decide | — |
| Reply interpreter fails | the reply item is FAILED; the baseline escalates if a reply exists but could not be read | — |
| Drafter fails or is unavailable | the pack template renders the message (`render_template`); a blocked draft escalates `policy_escalated` with detail `draft_blocked` (`policy-rules.md` section 8) | — |

Per-case call budget accounting: typically one interpreter call per reply, one investigator call per round, one drafter call per message. The kurta case with one reply and two messages uses 5 against `max_calls_per_case` = 6. The Phase 7 two-pass design (section 7) adds one investigator call per round that carries patterns, so `max_calls_per_case` rises to **10** when `cross_case` is enabled; `config.md` carries both values and startup picks by module status. Without that, the kurta case alone would need 7 and every patterned case would escalate `llm_budget_exceeded`.

## 5. Rules-only baseline (decision table)

`DomainPack.baseline_proposal(briefing)` is pure code; it is both the `rules_only` reasoning adapter and the fallback. First matching row wins.

| # | Condition | Action | parameters |
|---|-----------|--------|------------|
| B1 | Any required evidence item FAILED or MISSING | `escalate_to_human` | |
| B2 | Any manipulation flag, any screen flag, reply intent `contains_instruction_attempt` or `withdraws_dispute`, or a reply exists in the round that could not be read | `escalate_to_human` | |
| B3 | `definite_errors` has exactly one error, and no live or verified credit on the line | `propose_credit` | `reason_code` = that error |
| B4 | `definite_errors` has more than one error | `escalate_to_human` | |
| B5 | A seller reply in the round, any image evidence item, or any conflicting-history evidence item — each needs judgement the baseline cannot do | `escalate_to_human` | |
| B6 | A remeasurement is outstanding and nothing new | `wait` | |
| B7 | Charge follows the rules, measured size larger than declared beyond `baseline.tolerance_pct`, and no evidence request sent yet | `request_seller_evidence` | items: "Photo of the packed box beside a tape measure", "Packaging dimensions at dispatch" |
| B8 | Charge follows the rules, measured size ≤ declared size + `baseline.tolerance_pct`, and no live or verified credit on the line | `uphold_charge` | |
| B9 | Otherwise | `escalate_to_human` | |

The table is total (B9 is a catch-all) and unambiguous (first match wins). B5 is what makes the stated property true: **the baseline never judges free text or images**. A credit still needs no judgement — B3 fires on what code proved, whatever else is in the round — but once the deterministic results give no single definite error, a seller reply, an image or a conflicting history escalates before any row that would decide the case on its merits.

That is why S02, S03, S04 and S08 expect `escalate_to_human` from the baseline (`eval-scenarios.yaml`, `baseline:`): `kurta_box_larger` and `ambiguous_image` both carry an image evidence item and `conflicting_history` a history item, so B5 fires in the first round. S06 (`scanner_fault`) is the instructive opposite — it has no image, no history and no reply in round 1, so B7 fires and the baseline matches the AI's first action. The AI must beat the baseline where B5 escalates, without doing worse anywhere else (release gate `no_regression_vs_baseline`, compared **per round on the proposal action**, not on the final state).

B6 is placed above B8 deliberately: a case inside tolerance that is still waiting on our own hub waits rather than upholding, because upholding while a remeasurement we asked for is outstanding would decide the case on evidence we have not yet seen (fail safe, CLAUDE.md section 6).

The baseline is pure code and carries no review reason of its own: it returns `escalate_to_human`, and the orchestrator maps that to `proposal_escalated` (`orchestrator.md` section 3.6). The one exception is the fallback path, where `orchestrator.md` section 5 already records `module_unavailable:reasoning` (or `model_gateway`) on the `REASONER_FALLBACK`. B1's `module_unavailable:<m>` case needs no row here: `assess` escalates with that code before INVESTIGATING is reached.

`Proposal` requires three fields the baseline must still fill: `confidence` is always `high` (the table is deterministic, so there is nothing to be uncertain about and R22 therefore never fires on a baseline proposal); `findings` is a single `verified_fact` naming the row that matched, with `evidence_refs` citing the fact or evidence ids that satisfied its condition; `pattern_influence` is always `{considered: false, changed_recommendation: false, note: ""}`, since the baseline never reads `pattern_context`.

## 6. Where untrusted text can go

| Untrusted text | Reaches | Never reaches |
|----------------|---------|---------------|
| Seller reply | interpreter (`untrusted_seller_reply`), investigator (`untrusted_evidence`), review screen (boxed) | instructions, policy inputs other than labelled readings, recipients, amounts |
| Reply reading (AI-derived) | investigator (`untrusted_reply_reading`), policy rules R20/R21 (as labelled input, P5) | amounts, approval decisions |
| Ops free-text note | investigator (`untrusted_evidence`) | credit reasons (only structured fields count) |
| Dispute statement | investigator (`untrusted_evidence`) | facts used in messages |
| Proposal findings (AI) | drafter, clarification only (`untrusted_open_points`) | numbers in messages (R09) |
| `action_parameters.requested_items` (AI, may echo seller wording) | drafter (`untrusted_requested_items`), interpreter (`untrusted_outstanding_request`) — both rendered through `neutralise` | instructions, recipients, amounts; the drafter asks for them but never treats them as facts |
| Pattern summary produced by `pattern_analyst` (AI, derived from other cases' text) | investigator (`untrusted_pattern_context`) | verdicts, thresholds, amounts; it can only add caution (I7) |
| SELLER-provenance evidence item `value` | investigator (`untrusted_evidence`) | trusted facts, credit reasons, numbers in messages |

## 7. Two-pass reasoning (Phase 7 anchoring control)

Inside the `llm` reasoner adapter; the kernel sees one `Proposal`:
1. **Pass 1:** the briefing with `pattern_context` removed. Result P1.
2. **Pass 2** (only if patterns exist): the full briefing, plus a trusted block `<independent_view>` with P1's action and confidence. Result P2, whose `pattern_influence.changed_recommendation` must equal `P2.action != P1.action`; a mismatch is `InvalidModelOutput`.
3. The adapter returns P2 only if P2 is at least as cautious as P1. The caution order is **total**, strictest first: `escalate_to_human` > `request_remeasurement` > `request_seller_evidence` > `wait` > `uphold_charge` > `propose_credit`. `propose_credit` is the least cautious rank because it is the only action that moves money; `uphold_charge` sits just above it because it closes the case against the seller. If `rank(P2) < rank(P1)` the adapter returns P1 instead and records `pattern_relaxation_blocked`. Patterns may only add caution (I7), so a pattern can never turn an uphold into a credit.
4. The same applies within one action: if P2 keeps P1's action but raises `confidence` (`low` -> `medium` -> `high`) or changes `action_parameters`, the adapter keeps **P1's** `confidence` and `action_parameters` and records `pattern_relaxation_blocked`. Without this a pattern could lift a proposal out of R22's `low_confidence` escalation, which I7 and P5 forbid.
5. `pattern_influence.changed_recommendation` must equal `P2.action != P1.action`; a mismatch is `InvalidModelOutput`. The check is on the action alone, which is why steps 3 and 4 do not rely on it — the model's self-report is corroboration, not the control.
6. If P2 changed the action (in the more cautious direction), the kernel adds the `pattern_changed_recommendation` signal (min REQUIRE_APPROVAL).
7. Both passes count towards the per-case call budget; `max_calls_per_case` is 10 when `cross_case` is enabled (section 4).
