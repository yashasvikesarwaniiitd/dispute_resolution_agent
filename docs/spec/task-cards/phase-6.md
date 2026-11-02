# Phase 6 task cards — Evaluation and hardening

Stage A5. Source prompt: `docs/build-prompts/phase-6-eval-hardening.md`. Phase outcome: a complete scenario suite, metrics, explicit release gates, failure drills and documentation that states honestly what the results do and do not show.

**Phase defaults:** Sonnet, effort medium. Not marked safety-critical in `CLAUDE.md` section 17, but it is where the project decides what it is allowed to **claim**, which is its own kind of risk. P6-03 and P6-09 carry an Opus review for that reason.

**This phase resolves open decisions O3 (whether to use Foundry evaluations) and O6 (the gate thresholds).** Both need the owner.

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P6-01 | Complete the scenario simulator | L | P4-11, P5-* | |
| P6-02 | Metrics | L | P6-01 | |
| P6-03 | `config/release_gates.yaml` and `make eval` | M | P6-02 | yes |
| P6-04 | Failure drills | L | P6-01 | |
| P6-05 | Kill-switch drill | S | P1-08 | yes |
| P6-06 | Restart drill | M | P1-10 | |
| P6-07 | Report generator | M | P6-02 | |
| P6-08 | Security checklist review | M | P4b-12 | yes |
| P6-09 | Documentation and the honest-limits section | M | P6-07 | yes |
| P6-10 | Foundry evaluations (optional, O3) | M | P4b-11 | |
| P6-11 | Red team scan | M | P4b-03 | yes |
| P6-12 | Observability check | S | P4b-10 | |
| P6-13 | ADR: evaluation approach and thresholds | S | P6-03 | |

---

## P6-01 — Complete the scenario simulator

**Files:** `src/dispute_agent/modules/evals/scenarios/`.

**Implements:** `eval-scenarios.yaml` (all 59 scenarios), `FLOW.md` section 8.

**Build:** run the whole catalogue except the two Phase 7 scenarios (S15, S16). Every scenario has a human-reviewed expected outcome and expected final state — they are already written; this card makes them executable. Honour the catalogue's semantics exactly: `actions: n/a` is excluded from the action-rate denominator; `baseline:` compares **per round on the proposal action**; a `final:` naming a waiting state is a deliberate mid-flight stop, not an outcome.

The message-review scenario the prompt asks for already exists in shape — a review-required message waits for approval and is then sent **unchanged** (the hash check, I13).

**Tests first:** `python scripts/check_scenarios.py .` must still pass; then each scenario executes and matches its declared expectations.

**Acceptance:** `make eval` runs all 57 non-Phase-7 scenarios.

**Size:** L. **Model:** Sonnet, effort medium.

**Reuse:** `scripts/check_scenarios.py` already parses the catalogue and the transition table and validated every path during Stage A5. The runner should share its reading rather than writing a second interpretation.

---

## P6-02 — Metrics

**Files:** `src/dispute_agent/modules/evals/metrics/`.

**Implements:** `observability.md` section 4, `release-gates.yaml` (the `eval_metric_definitions` block).

**Build:** the runtime metrics from `observability.md` — correct-action rate, successful resolution rate, autonomous resolution rate, escalation rate with reasons, unauthorised financial actions (**must be 0**), duplicate side-effect rate (**must be 0**), ledger verification failures, evidence completeness, time to resolution, cost per resolved case per route. Plus the nine **eval-only** metrics defined in `release-gates.yaml`, which have no runtime counterpart.

Mind the denominators — `release-gates.yaml` states them per gate precisely because several differ from the runtime definition. The escalation-rate gate uses "scenarios not expecting a person", not all cases; `correct_final_outcome_rate` counts a scenario that must end in `NEEDS_HUMAN_REVIEW` and does so as **correct**.

**Tests first:** one test per metric with a hand-computed fixture, including a case that distinguishes the gate denominator from the runtime one.

**Acceptance:** `pytest tests/unit/modules/evals/test_metrics.py -q`.

**Size:** L. **Model:** Sonnet, effort medium.

---

## P6-03 — `config/release_gates.yaml` and `make eval`

**Files:** `config/release_gates.yaml`, `src/dispute_agent/modules/evals/gates.py`.

**Implements:** `release-gates.yaml` (the Stage A4 draft), open decision O6, `config.md` section 6.

**Build:** copy the draft from `docs/spec/release-gates.yaml` into `config/release_gates.yaml` — thresholds live in **configuration, not code**. `make eval` exits non-zero when any gate fails **or is null**. The draft's values become real here, which is O6: the owner approves the thresholds against the first real measurements.

**Tests first:**
- `test_gates_fail_closed.py` — a null threshold fails the release; a passing suite with one hard-gate breach fails regardless of everything else.
- `test_gate_metrics_defined.py` — every gate names a metric that exists, in `observability.md` or the eval-only block.

**Acceptance:** `make eval` exits non-zero on a seeded breach.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes. **Stop and ask:** the thresholds are O6 — the owner approves the numbers.

---

## P6-04 — Failure drills

**Files:** `tests/e2e/drills/`.

**Implements:** the phase prompt task 4, `test-charter.md`.

**Build:** email and ticket API failures (S12a, S12b, S12c); ambiguous ledger result (S13a, S13b); late reply after timeout (S09a, S09b, S43d); silent seller timers (S08, S36); duplicate events and retries (S11); concurrent events on one case (ADR 0008's optimistic versioning under contention).

**Tests first:** the drills are the deliverable. The concurrency drill needs two writers racing on one case; assert exactly one wins and the other retries cleanly.

**Acceptance:** `pytest tests/e2e/drills -q`.

**Size:** L. **Model:** Sonnet, effort medium.

---

## P6-05 — Kill-switch drill

**Files:** `tests/e2e/drills/test_kill_switch.py`.

**Implements:** I10, scenario S19.

**Build:** turn the switch on mid-flow; **no external write may occur afterwards**. Prove it at the executor level, not just the entry point — the switch is checked inside `plan` and `execute` (I10). Scenario S19 then resumes the case after the switch is released.

**Tests first:** the drill is the deliverable. Assert **zero adapter calls** while the switch is on, by counting calls on the fake, not by inspecting state afterwards.

**Acceptance:** `pytest tests/e2e/drills/test_kill_switch.py -q`.

**Size:** S. **Model:** Sonnet, effort medium. **Opus review:** yes (I10).

---

## P6-06 — Restart drill

**Files:** `tests/e2e/drills/test_restart.py`.

**Implements:** the phase prompt task 6, `orchestrator.md` section 6.

**Build:** stop the process at **each** waiting state — `AWAITING_SELLER`, `AWAITING_OPS`, `AWAITING_APPROVAL`, and resting in `GATHERING_EVIDENCE` and `VERIFYING` — and resume. Timers are rows, so nothing should be lost; this drill is what proves it.

**Tests first:** the drill is the deliverable, parametrised over the waiting states.

**Acceptance:** `pytest tests/e2e/drills/test_restart.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P6-07 — Report generator

**Files:** `src/dispute_agent/modules/evals/reports/`.

**Implements:** the phase prompt task 7.

**Build:** a markdown or HTML report with per-scenario results and metric tables, including **cost per case from `model_calls`** and the baseline-versus-LLM comparison. The report is what the project shows; it must carry the scenario count and run count on every figure, because `release-gates.yaml` forbids quoting accuracy without them.

**Tests first:** `test_report.py` — a report from a known run contains every scenario, and every percentage is accompanied by its denominator.

**Acceptance:** `make eval` writes the report.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P6-08 — Security checklist review

**Files:** `docs/SECURITY_REVIEW.md`.

**Implements:** `threat-model.md` (T01-T21), I11, I12, I14.

**Build:** review and record: least privilege on the Outlook scopes and the Jira token (O5); log redaction working end to end; the prompt-injection suite passing by all four entry routes; secret scanning in CI (P4b-12). Walk `threat-model.md` row by row and name the test that covers each — the table claims every threat has one, so this card verifies that claim rather than assuming it.

**Tests first:** `test_threat_coverage.py` — every threat id in `threat-model.md` names a test id that exists.

**Acceptance:** the checklist is complete and every threat maps to a passing test.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes.

---

## P6-09 — Documentation and the honest-limits section

**Files:** `README.md`, `docs/DEMO.md`, `docs/MODULES.md`.

**Implements:** `release-gates.yaml` (`claims_not_to_make`), D2, I11.

**Build:** a README with a Mermaid architecture diagram, the demo walkthrough, the **honest-limits section** and the module on/off guide. The limits section is the point of this card: the project is a synthetic pilot, every credit and in the pilot every message is approved by a person, no figure is any company's real tariff, and "verified" for email means in Sent Items with the approved text — not delivered.

**Tests first:** `test_claims.py` — grep the README and the generated reports for every phrase in `claims_not_to_make`; fail on a hit. This is the test `release-gates.yaml` says Phase 6 adds.

**Acceptance:** `pytest tests/unit/test_claims.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes — this is where overclaiming would do real reputational damage.

---

## P6-10 — Foundry evaluations (optional, O3)

**Files:** an ADR; eval outputs.

**Implements:** open decision O3.

**Build:** optional and supplementary — **local gates stay authoritative** (O3). If run: choose the evaluators that fit tool-less agents and record the choice in an ADR, then run them against the hosted prompt agents on the scenario inputs.

**Acceptance:** either the ADR records the decision not to use them, or results exist alongside the local report.

**Size:** M. **Model:** Sonnet, effort medium. **Stop and ask:** O3 is the owner's call.

---

## P6-11 — Red team scan

**Files:** `tests/evals/red_team/`, an ADR.

**Implements:** the phase prompt task 12, D35 (local red teaming), threat T01.

**Build:** run the Foundry AI Red Teaming scan (**preview** — keep it inside the adapter and out of `make verify`, `HANDOFF.md` section 9) against the hosted agents with the injection scenarios. **Every failure becomes a new local scenario** — that is the card's real output, not the scan report.

**Tests first:** any failure found becomes a scenario in `eval-scenarios.yaml` with a declared path, then `scripts/check_scenarios.py` must still pass.

**Acceptance:** the scan has run; every failure has a local scenario; the suite still passes.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes.

**Watch:** this must happen inside the Azure credit window (D18).

---

## P6-12 — Observability check

**Files:** `tests/integration/test_observability.py`.

**Implements:** I14, `observability.md`.

**Build:** traces reach Application Insights with **no message content**; the eval report shows cost per case from `model_calls`.

**Tests first:** a sentinel content run whose exported spans contain none of it.

**Acceptance:** `pytest tests/integration/test_observability.py -q` with the connection string set.

**Size:** S. **Model:** Sonnet, effort low.

---

## P6-13 — ADR: evaluation approach and gate thresholds

**Files:** `docs/adr/00NN-evaluation-and-gates.md`.

**Build:** the evaluation approach and the reasoning behind **each** threshold. `docs/spec/release-gates.yaml` already carries a `why` per gate from Stage A4; this ADR records what the first real measurements changed, and resolves O6 with the owner's approval.

**Acceptance:** ADR written; O6 resolved; `docs/PROGRESS.md` updated.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green; `make eval` runs the full suite, enforces the gates and writes the report; every drill passes; every threat maps to a test; the README states its limits honestly and the claims test passes; O3 and O6 resolved; `/phase-verify 6`; `/session-handoff`.
