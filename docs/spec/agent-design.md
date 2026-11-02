# Agent design (Stage A4)

Status: draft for owner approval. What the AI sees, what it may answer, what happens when it fails, the rules-only baseline it is measured against, and the two-pass design for Phase 7. Models: `kernel-interfaces.md` section 4.7; prompts: `prompts/`; review: `prompt-review.md`.

Plain-language summary: the AI sees a carefully built summary of one case, never the raw database, and it can answer only with one of a few allowed next steps. Facts calculated by code are labelled as verified; anything a seller wrote is fenced off and labelled as untrusted. If the AI misbehaves, runs out of budget or is blocked, the case goes to a person. A simple rules-only version handles the clear-cut cases with no AI at all, and every AI result is compared against it.

## 1. The briefing (`CaseBriefing` -> template context `b`)

The briefing is built by code (`orchestrator/briefing.py`) and rendered by `user.md.j2`. The fields the template uses, with their caps:

| Template field | From | Trust | Cap and truncation |
|----------------|------|-------|--------------------|
| `b.case_ref` | display reference `CASE-<n>` | system | — |
| `b.facts.shipment_ref`, `claim_ref`, `seller_ref`, `raised_at` | `CaseFacts` | system | identifiers ≤ 64 chars |
| `b.assessment_view` | `ChargeAssessment`: `eligible`, `charged`, `recalculated`, `recalculated_remeasured`, `discrepancy`, `definite_errors`, and `facts` (`fact_id`, `label`, rendered value) | system | ≤ 40 facts |
| `b.trusted_evidence[]` | evidence items with provenance SYSTEM or OPS structured fields: `id`, `source`, `status`, `value` (JSON) | system | ≤ 30 items; each value ≤ 2,000 chars of JSON, else replaced by `{"truncated": true, "keys": [...]}` |
| `b.untrusted_evidence[]` | every `UntrustedText` in the round (dispute statement, seller replies, ops notes, shipment notes): `id`, `source`, `text` | outside party | ≤ 5 items, newest first; each ≤ 4,000 chars (head 3,000 + tail 1,000 with a `[... n characters omitted ...]` marker); the full text stays in the database |
| `b.reply_reading` | latest `ReplyReading` (AI-derived) | outside party | its schema caps (summary ≤ 300) |
| `b.contact` | `reply_in_round`, `clarifications_sent`, `remeasurement_outstanding` (OPS_TIMEOUT active) | system | — |
| `b.related_cases[]` | `CaseRepository.related`: `case_ref`, `state`, `closed_at` | system | ≤ 10, newest first; outcome only, no seller text |
| `b.pattern_context[]` | Phase 7 patterns: `id`, `status`, `summary` | system-produced hypotheses | ≤ 5; summary ≤ 400 |
| `b.prior_rejections[]` | approval rejection notes in this round | staff | ≤ 3; each ≤ 500 |
| `b.allowed_actions[]` | section 2 | system | non-empty |

Total rendered user message ≤ 24,000 characters. If a briefing would exceed it, untrusted texts are shortened further (oldest first) and then related cases dropped. Trusted facts are never dropped; if they alone exceed the budget the case escalates `internal_error` (a pack bug). The `briefing_hash` is computed over the rendered message, so a proposal can be reproduced exactly.

Reply-interpreter context (`ctx` in its template): `case_ref`, `request_sent_at`, `requested_items` (from the last verified evidence request or clarification), `reply_text` (≤ 20,000 chars, I5), `attachments` (name, content type). Drafter context: `kind`, `case_ref`, `facts` (approved facts with `kind` and `rendered`), `requested_items`, `open_points` (clarification only: the `missing`/`partial` requested items and the proposal's missing- and conflicting-evidence statements), `next_step`, `style` (tone `polite`, language from the seller's last reply or `en`, max words 180).

## 2. Allowed actions (the menu)

The reasoner runs only in INVESTIGATING. The menu is the pack's `allowed_actions(state, assessment)` intersected with the kernel filters:

| Situation | Menu |
|-----------|------|
| Eligible, assessment complete, no credit on the claim line | all six actions |
| `definite_errors` empty | drops `propose_credit` (the baseline and R14 would refuse it anyway) |
| The shipment has a live or verified credit (kernel filter) | drops `propose_credit` and `uphold_charge` (R18) |
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

## 4. Failure handling

| Failure | Result | Failover (F01)? |
|---------|--------|-----------------|
| Invalid JSON or schema, or a check in section 3 fails | one identical retry; then escalate `invalid_model_output` | no |
| Output cut short (`max_output_tokens`) or Anthropic `refusal` | as invalid | no |
| Per-case calls exhausted (M01) | escalate `llm_budget_exceeded` | no |
| Spend cap (M03/M05) | escalate `credit_cap_reached` | no |
| Guardrail block | escalate `guardrail_blocked` | no |
| Agent drift | escalate `agent_drift` | no |
| Provider unavailable, rate-limited, quota, credit window | Haiku secondary | yes |
| Both routes unavailable, or `reasoning`/`model_gateway` disabled | rules-only baseline (section 5) with `fallback_used = true`; it escalates `module_unavailable:<module>` when it cannot decide | — |
| Reply interpreter fails | the reply item is FAILED; the baseline escalates if a reply exists but could not be read | — |
| Drafter fails or is unavailable | the pack template renders the message (`render_template`); a blocked draft escalates `draft_blocked` | — |

Per-case call budget accounting (M01 = 6): typically one interpreter call per reply, one investigator call per round, one drafter call per message. The kurta case with one reply and two messages uses 5.

## 5. Rules-only baseline (decision table)

`DomainPack.baseline_proposal(briefing)` is pure code; it is both the `rules_only` reasoning adapter and the fallback. First matching row wins.

| # | Condition | Action | reason / parameters |
|---|-----------|--------|---------------------|
| B1 | Any required evidence item FAILED or MISSING | `escalate_to_human` | `module_unavailable:<m>` if the error code says so, else a failed critical source |
| B2 | Any manipulation flag, screen flag, or reply intent `contains_instruction_attempt` or `withdraws_dispute` | `escalate_to_human` | |
| B3 | `definite_errors` has exactly one error, and no live or verified credit on the line | `propose_credit` | `reason_code` = that error |
| B4 | `definite_errors` has more than one error | `escalate_to_human` | |
| B5 | Charge follows the rules, measured size ≤ declared size + `baseline.tolerance_pct` (5%), and no reply yet | `uphold_charge` | |
| B6 | Charge follows the rules, measured size larger than declared beyond tolerance, no reply yet, no evidence request sent yet, and no image or conflicting-history evidence item in the round (those need judgement: B8) | `request_seller_evidence` | items: "Photo of the packed box beside a tape measure", "Packaging dimensions at dispatch" |
| B7 | A remeasurement is outstanding and nothing new | `wait` | |
| B8 | Otherwise (any seller reply to judge, any conflicting history, any image) | `escalate_to_human` | `module_unavailable:reasoning` when running as the fallback |

The baseline never reads free text: replies, images and history always reach B8. That is why S02, S03, S04 and S06 expect `escalate_to_human` from the baseline (`eval-scenarios.yaml`, `baseline:`). The AI must beat it there without doing worse anywhere else (release gate `no_regression_vs_baseline`).

## 6. Where untrusted text can go

| Untrusted text | Reaches | Never reaches |
|----------------|---------|---------------|
| Seller reply | interpreter (`untrusted_seller_reply`), investigator (`untrusted_evidence`), review screen (boxed) | instructions, policy inputs other than labelled readings, recipients, amounts |
| Reply reading (AI-derived) | investigator (`untrusted_reply_reading`), policy rules R20/R21 (as labelled input, P5) | amounts, approval decisions |
| Ops free-text note | investigator (`untrusted_evidence`) | credit reasons (only structured fields count) |
| Dispute statement | investigator (`untrusted_evidence`) | facts used in messages |
| Proposal findings (AI) | drafter, clarification only (`untrusted_open_points`) | numbers in messages (R09) |

## 7. Two-pass reasoning (Phase 7 anchoring control)

Inside the `llm` reasoner adapter; the kernel sees one `Proposal`:
1. **Pass 1:** the briefing with `pattern_context` removed. Result P1.
2. **Pass 2** (only if patterns exist): the full briefing, plus a trusted block `<independent_view>` with P1's action and confidence. Result P2, whose `pattern_influence.changed_recommendation` must equal `P2.action != P1.action`; a mismatch is `InvalidModelOutput`.
3. The adapter returns P2. If P2 changed the action, the kernel adds the `pattern_changed_recommendation` signal (min REQUIRE_APPROVAL). If P2 is *less* cautious than P1 (an order: escalate > request_* > wait > uphold/credit), the adapter returns P1 instead and records `pattern_relaxation_blocked`: patterns may only add caution (I7).
4. Both passes count towards M01 (calls per case).
