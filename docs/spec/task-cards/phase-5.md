# Phase 5 task cards — Approvals and ledger

Stage A5. Source prompt: `docs/build-prompts/phase-5-approvals-ledger.md`. Phase outcome: a credit can only happen after a stored human approval, is posted idempotently to a simulated ledger, and is independently verified before the case can resolve.

**Phase defaults:** Sonnet, effort medium. **`CLAUDE.md` section 17 marks this phase for a mandatory Opus review** — it is the only phase that moves money, even simulated money. Run `safety-reviewer` before closing it, and treat every card below as touching I3.

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P5-01 | Ledger simulator | L | P1-01 | |
| P5-02 | `Ledger` contract suite | M | P5-01 | |
| P5-03 | Credit approvals in the review queue | M | P3-06 | yes |
| P5-04 | The credit flow | L | P5-03, P1-08 | yes |
| P5-05 | Credit verification | L | P5-01, P5-04 | yes |
| P5-06 | Reason codes, limits and amount checking | M | P2-07 | yes |
| P5-07 | Registry rule scenario: ledger requires approvals | S | P0-07 | |
| P5-08 | Upheld and credit-confirmation templates | M | P3-05 | |
| P5-09 | Full single-case demo | M | P5-05, P3-10 | |
| P5-10 | ADRs | S | P5-05 | |

---

## P5-01 — Ledger simulator

**Files:** `src/dispute_agent/modules/ledger/simulated/`.

**Implements:** `money-rules.md` sections 5-7, `kernel-interfaces.md` (`Ledger`), D4 (the ledger is simulated).

**Build:** behave like a real ledger, with **its own SQLite store** separate from the case database. Transaction ids; `post_credit` idempotent on the key; `get_transaction`; the four wallet transaction types. Support injected timeouts, duplicates and ambiguous results per `money-rules.md` section 7 (`timeout_before`, `timeout_after`, `wrong_amount`, …).

**Critical:** the simulator shares **no code path** with the kernel's logic. Verification in P5-05 is only meaningful if the thing being verified was produced independently of the thing verifying it. A shared helper that computes the amount for both sides makes every verification test vacuous.

**Tests first:** `test_ledger_simulator.py` — `post_credit` twice with one key gives one transaction; each fault token behaves as `money-rules.md` section 7 states.

**Acceptance:** `pytest tests/unit/modules/ledger -q`.

**Size:** L. **Model:** Sonnet, effort medium.

---

## P5-02 — `Ledger` contract suite

**Files:** `tests/contract/test_ledger.py`.

**Implements:** `CLAUDE.md` section 8.

**Build:** fill the Phase 0 skeleton. Cases: post returns a reference; `get_transaction` reads back amount, currency, seller and case; the same key twice yields one transaction; an ambiguous result is reported as ambiguous, never as success.

**Tests first:** the suite is the deliverable; run it against the simulator.

**Acceptance:** `pytest tests/contract/test_ledger.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P5-03 — Credit approvals in the review queue

**Files:** `src/dispute_agent/modules/approvals/` (extend), `streamlit_ui/credits.py`.

**Implements:** `approvals.md`, ADR 0013, D6, D16, D40, I3.

**Build:** extend the Phase 3 module to credits. The queue lists pending credit approvals with the **evidence summary and the calculated correction**, so the approver sees why, not just how much. Record approver, time and reason. Per D40, **rejecting a credit escalates `credit_rejected`** rather than re-planning — the reviewer disagreeing with a code-proven amount means a person owns the case (transition X15, T90 rather than T11).

**Tests first:**
- `test_credit_rejection_escalates.py` — scenario S10: rejecting gives `credit_rejected`, `ledger_credits: 0`, and the case does not re-plan.
- `test_queue_shows_evidence.py` — the pending item carries the assessment facts and the correction.

**Acceptance:** `pytest tests/unit/modules/approvals/test_credits.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes.

---

## P5-04 — The credit flow

**Files:** `src/dispute_agent/kernel/orchestrator/` (the credit path), `kernel/executor/`.

**Implements:** `transitions.yaml` (T10, T17, T24, X15), `approvals.md`, `policy-rules.md` R13-R18, I3.

**Build:** `propose_credit` with a reason code, then policy (**always** requires approval in the pilot), then `AWAITING_APPROVAL`, then `EXECUTING` on a valid approval, then `VERIFYING`, then `RESOLVED_CREDITED`. The executor finds the approval itself: bound, APPROVED, unused, amount equal, hash matching (I3). State machine check C7 — credits reach `EXECUTING` **only** from `AWAITING_APPROVAL`, enforced by the `intent_is_not_credit` guard everywhere else.

**Tests first:**
- `test_credit_requires_approval.py` — **call the executor directly** with a credit intent and no approval record: refused. This is the phase prompt's named negative test and the sharpest form of I3.
- `test_approval_reuse_refused.py` — an approval already used by another action cannot be used again (`approvals.used_by_action_id UNIQUE`).
- `test_amount_mismatch_refused.py` — an approval for 150.00 cannot serve an intent of 150.01.

**Acceptance:** `pytest tests/unit/executor/test_credit_flow.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I3).

---

## P5-05 — Credit verification

**Files:** `src/dispute_agent/kernel/executor/verify_credit.py`.

**Implements:** `executor.md` section 6, ADR 0010, ADR 0012, I4, I3.

**Build:** verification reads back from the ledger and checks: the transaction exists; amount and currency match; it belongs to the right seller and case; there is no duplicate; the ledger reports success. **Any failure or uncertainty goes to `NEEDS_HUMAN_REVIEW`** (I4) — the action becomes UNKNOWN, and a new credit for that shipment is blocked until a person resolves it. Per ADR 0012, "resolved credited" means credited **and told**: the confirmation message is part of the outcome, not an afterthought.

**Tests first:**
- scenario S13a (response lost after the credit posted — found by lookup, resolves).
- scenario S13b (ledger posts the wrong amount — UNKNOWN, `verification_failed`, never resolved).
- scenario S25 (credit posted, confirmation email fails — `HANDED_TO_HUMAN`, `ledger_credits: 1`, and the case can never return to the agent).

**Acceptance:** `pytest tests/e2e/test_credit_verification.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I3, I4).

---

## P5-06 — Reason codes, limits and amount checking

**Files:** `src/dispute_agent/kernel/policy/rules/credit_rules.py`, `config/policy.yaml`.

**Implements:** `policy-rules.md` R14-R18, `money-rules.md` section 4, D29, D30, I3, I9.

**Build:** reason codes from `policy.yaml`; only the three code-proven reasons are valid. An amount differing from the deterministic expected correction is **rejected by policy** (I9) — the amount comes from the assessment, never from model output. Per D30 there are **no amount caps**: a large credit takes the same path as a small one, and approval is what gates it. R14 denies `credit_reason_unproven` and escalates `multiple_definite_errors` when the assessment lists more than one.

**Tests first:**
- `test_R14.py` — a reason not in `definite_errors` is denied; two definite errors escalate (scenario S38).
- `test_no_amount_caps.py` — the same case at INR 150 and INR 1,820 takes an identical path (scenario S39 versus S01), proving D30.
- `test_R17_R18.py` — a second credit for the same shipment and reason is denied across cases; a reopened credited case cannot uphold.

**Acceptance:** `pytest tests/unit/policy/test_credit_rules.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes (I3, I9).

---

## P5-07 — Registry rule scenario: ledger requires approvals

**Files:** `tests/e2e/test_unsafe_combination.py`.

**Implements:** `ARCHITECTURE.md` section 5, I3, I6.

**Build:** the registry already refuses `ledger` enabled with `approvals` disabled (P0-07). Add the **scenario** test the phase prompt asks for: startup refuses the combination, and the message names both modules. Also scenario S41's ledger and approvals variants — each disabled module escalates `module_unavailable:<name>` when a case needs it.

**Tests first:** the tests are the deliverable.

**Acceptance:** `pytest tests/e2e/test_unsafe_combination.py -q`.

**Size:** S. **Model:** Sonnet, effort low.

---

## P5-08 — Upheld and credit-confirmation templates

**Files:** `src/dispute_agent/packs/weight_dispute/templates/{upheld_explanation,credit_confirmation}.md.j2`.

**Implements:** `policy-rules.md` R09, R18, I9, I13.

**Build:** both templates, both through the Phase 3 message review queue — which kinds need review stays a setting in `policy.yaml` (D6, O7). `credit_confirmation` **must** cite `credited_amount` or block: a confirmation with no amount is a blocked draft, not a vague message (the drafter's MD-F6 rule).

**Tests first:** `test_credit_confirmation_requires_amount.py` — rendering without `credited_amount` yields `blocked_reason: missing_fact: credited_amount`, never a blank. Plus: every number in both templates traces to an approved fact.

**Acceptance:** `pytest tests/unit/packs/test_templates.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P5-09 — Full single-case demo

**Files:** `scripts/demo.py` (extend).

**Implements:** the phase prompt task 8.

**Build:** extend `make demo` to the full single-case sequence: dispute, evidence, recommendation, real email, real reply, resume, real Jira issue, approval, ledger credit, verification, timeline. This is the demonstration the project is for — it should run from one command and end with a timeline a person can read.

**Tests first:** `test_demo_sequence.py` — the same sequence against fakes completes and produces every expected artefact, so the demo cannot silently rot between runs with real credentials.

**Acceptance:** `make demo` completes end to end.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P5-10 — ADRs

**Files:** `docs/adr/`.

**Build:** ADRs 0012 and 0013 already exist and are accepted. Add implementation notes, plus new ADRs where this phase decided something genuinely new: ledger idempotency and ambiguous-outcome handling.

**Acceptance:** `docs/PROGRESS.md` updated.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green; a credit cannot be executed without an approval record even when the executor is called directly; the ledger simulator shares no code path with the kernel; `make demo` runs the full single-case sequence; `/phase-verify 5`; **run `safety-reviewer` — mandatory for this phase**; `/session-handoff`.
