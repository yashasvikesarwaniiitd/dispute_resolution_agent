# Phase 2 task cards — Weight-dispute pack

Stage A5. Source prompt: `docs/build-prompts/phase-2-weight-pack.md`. Phase outcome: for a synthetic disputed shipment the system gathers evidence from each source, records what is missing or failed, recalculates the charge deterministically, and reconciles it against a simulated ledger.

**Phase defaults:** Sonnet, effort medium. Not a safety-critical phase by `CLAUDE.md` section 17, but P2-02 and P2-06 decide every number the system will later put in front of a seller (I9), so both carry an Opus review.

**The pack rule:** `packs/` imports `kernel` only (enforced by `make arch`). The pack holds domain knowledge; it never reaches a database, a channel or a model.

| Card | Title | Size | Depends on | Opus review |
|------|-------|------|-----------|-------------|
| P2-01 | `DomainPack` protocol and the weight-dispute skeleton | M | P1-06 | |
| P2-02 | The calculator | L | P2-01 | yes |
| P2-03 | Rate card, zones and slabs as configuration | M | P2-02 | |
| P2-04 | Seeded synthetic generator | L | P2-01 | |
| P2-05 | `SyntheticShipmentSource` with injectable faults | M | P2-04 | |
| P2-06 | Evidence service and the gather round | L | P2-05 | yes |
| P2-07 | Ledger reconciliation and definite errors | L | P2-02, P2-04 | yes |
| P2-08 | The rules-only baseline | M | P2-01, P2-07 | yes |
| P2-09 | Wire evidence and calculation into the orchestrator | M | P2-06, P2-07 | |
| P2-10 | Calculator property tests | M | P2-02 | |
| P2-11 | ADR: public facts, assumptions and why every figure is configuration | S | P2-03 | |

---

## P2-01 — `DomainPack` protocol and the weight-dispute skeleton

**Files:** `src/dispute_agent/kernel/ports/domain_pack.py`, `src/dispute_agent/packs/weight_dispute/{__init__,pack}.py`, `eligibility.py`.

**Implements:** `kernel-interfaces.md` (`DomainPack`), `agent-design.md` section 2 (`allowed_actions`), `policy-rules.md` (eligibility).

**Build:** the `DomainPack` port with `eligibility`, the evidence plan, `assess`, `allowed_actions(state, assessment)` and `baseline_proposal(briefing)`. Eligibility: claims only on delivered shipments. `allowed_actions` returns the pack's menu; the kernel then subtracts `propose_credit` and `uphold_charge` when the case has a live or verified credit (`orchestrator.md` section 3.6) — the pack does not know about credits on other cases, so it must not try to.

**Tests first:** `tests/unit/packs/test_eligibility.py` — a delivered shipment is eligible; an RTO shipment is `ineligible(not_delivered)` (fixture `not_delivered`); a shipment whose status cannot be determined escalates rather than guessing (scenario S46).

**Acceptance:** `pytest tests/unit/packs/test_eligibility.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P2-02 — The calculator

**Files:** `src/dispute_agent/packs/weight_dispute/calculator.py`.

**Implements:** `money-rules.md` sections 1-3, ADR 0011 (integer paise and grams), D10 (the divisor is configuration), I9.

**Build:** volumetric weight = L x B x H (cm) / divisor, divisor configurable, default 5000 from public documentation (D10). Chargeable weight: dead weight below the configured threshold; the greater of dead and volumetric above it; packaging-type exceptions. **All** figures come from configuration — no literal rate, slab, zone or threshold in the code. `Decimal` throughout; money stored as integer paise and weight as integer grams (ADR 0011); rounding documented at each step and applied once, at the stated point, not repeatedly.

**Tests first:**
- `test_calculator_kurta.py` — the kurta fixture: declared 20x15x10 cm and 480 g, measured 30x22x15 cm and 1,100 g, zone B, gives volumetric declared 600 g, measured 1,980 g, chargeable 1,980 g, 4 slabs, INR 152.00 (`synthetic-data.md` section 4). The worked example in `FLOW.md` section 7a must reproduce exactly.
- `test_no_floats.py` — an architecture-style test asserting no `float` appears in the calculator's call graph.

**Acceptance:** `pytest tests/unit/packs/test_calculator.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I9) — every number in every outbound message traces back to this file.

**Watch:** `calc_error_high_value` is the arithmetic that catches sloppy rounding: zone E at INR 70.00 per slab, true chargeable 7,000 g = 14 slabs = INR 980.00, billed on a keyed 20,000 g = 40 slabs = INR 2,800.00, credit INR 1,820.00 = 26 slabs x INR 70.00. If rounding is applied per slab rather than once, this comes out wrong by rupees.

---

## P2-03 — Rate card, zones and slabs as configuration

**Files:** `src/dispute_agent/packs/weight_dispute/rate_card.py`, `config/policy.yaml` (the rate section).

**Implements:** `money-rules.md`, `config.md`, D2, D10.

**Build:** zones, slab size, per-slab rates per zone and the volumetric divisor, all loaded from configuration and validated at startup (fail fast). Synthetic, illustrative values only — the repository states it is independent and synthetic (D2), and no figure claims to be any company's real tariff.

**Tests first:** `test_rate_card_validation.py` — a missing zone, a negative rate and a zero divisor each fail at startup with a message naming the key.

**Acceptance:** `pytest tests/unit/packs/test_rate_card.py -q`.

**Size:** M. **Model:** Sonnet, effort low.

---

## P2-04 — Seeded synthetic generator

**Files:** `src/dispute_agent/modules/evidence/synthetic_shipments/generator.py`, `fixtures.py`.

**Implements:** `synthetic-data.md` in full — knobs, distributions, seeds, the 15 named fixtures, the reply bank and the ops-finding bank.

**Build:** a seeded generator over sellers, SKUs, hubs, scanners, shipments, measurements, image placeholder references and ledger entries including the four wallet transaction types. Same seed, same world, every run (`CLAUDE.md` section 10). All 15 named fixtures are constructible by name, including the four the Stage A4 review added: `duplicate_debit`, `multi_error`, `not_delivered_late_scan`, `injection_in_ops_note`.

**Tests first:**
- `test_generator_deterministic.py` — the same seed twice produces byte-identical worlds.
- `test_fixtures_exist.py` — every fixture named in `synthetic-data.md` section 4 and every reply and finding template referenced by `eval-scenarios.yaml` can be constructed. Read both files at test time so a new scenario cannot reference a fixture that does not exist.

**Acceptance:** `pytest tests/unit/modules/evidence -q`.

**Size:** L. **Model:** Sonnet, effort medium.

---

## P2-05 — `SyntheticShipmentSource` with injectable faults

**Files:** `src/dispute_agent/modules/evidence/synthetic_shipments/source.py`.

**Implements:** `kernel-interfaces.md` (`EvidenceSource`, `EvidenceResult`), `synthetic-data.md` section 5 (the fault vocabulary).

**Build:** `fetch(ref)` returns found, missing or failed — **never an invented value**. Supports the fault tokens of `synthetic-data.md` section 5: `timeout`, `timeout_twice`, `error_permanent`, `unavailable`, and the rest. A transient failure is distinguishable from a permanent one, because D41 retries the first and not the second.

**Tests first:** `test_source_faults.py` — one test per fault token, asserting the result kind and, for `error_permanent`, that no retry timer is armed (scenario S21).

**Acceptance:** `pytest tests/unit/modules/evidence/test_source.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P2-06 — Evidence service and the gather round

**Files:** `src/dispute_agent/kernel/orchestrator/evidence.py`.

**Implements:** `orchestrator.md` (the `gather_evidence` step), `transitions.yaml` (`steps.gather_evidence`), D41, I4.

**Build:** gather from every planned source, persist an `evidence_items` row per source with its status, and **never invent a missing value** (I4). A required item that fails transiently arms `EVIDENCE_RETRY` with the current `gather_round`, at most three per round (D41); the step rests meanwhile. A permanent failure records FAILED at once with no retry. A FAILED required item is visible to `assess` and to the reasoner — it is not hidden.

**Tests first:**
- `test_gather_round.py` — scenario S05a (three retries 15 minutes apart, then FAILED), S05c (recovers on the second retry), S21 (permanent, no retry).
- `test_never_invents.py` — with every source failing, no evidence item carries a value.

**Acceptance:** `pytest tests/unit/orchestrator/test_evidence.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I4) — this is where "unknown" must stay unknown.

---

## P2-07 — Ledger reconciliation and definite errors

**Files:** `src/dispute_agent/packs/weight_dispute/reconcile.py`, `src/dispute_agent/modules/ledger/simulated/`.

**Implements:** `money-rules.md` section 4 (the three code-proven credit reasons), section 2 (structured fields only), I9, I3.

**Build:** compare the expected charge with the ledger charge; detect duplicate transactions; find existing credits or reversals; and produce `definite_errors` — the **only** basis for a credit (D29, R14). The three proven reasons and nothing else: `calculation_error`, `remeasurement_lower`, `duplicate_charge`. A reason comes from structured fields only; free text never contributes (`agent-design.md` section 6).

**Tests first:**
- one test per reason, using its fixture: `calc_error_wrong_slab`, `scanner_fault` after the finding, `duplicate_debit`.
- `test_multi_error.py` — the `multi_error` fixture yields two definite errors, and therefore no credit (R14, scenario S38).
- `test_free_text_never_proves.py` — a seller note claiming a duplicate charge, with a clean ledger, yields no definite error.

**Acceptance:** `pytest tests/unit/packs/test_reconcile.py -q`.

**Size:** L. **Model:** Sonnet, effort medium. **Opus review:** yes (I3, I9).

---

## P2-08 — The rules-only baseline

**Files:** `src/dispute_agent/packs/weight_dispute/baseline.py`, `src/dispute_agent/modules/reasoning/rules_only/`.

**Implements:** `agent-design.md` section 5 (rows B1-B9), `kernel-interfaces.md` section 15.2.

**Build:** `DomainPack.baseline_proposal(briefing)` as pure code, first match wins, rows B1-B9 exactly in order. It is both the `rules_only` reasoning adapter and the fallback when the model is unavailable. It returns a full `Proposal`: `confidence` always `high`, one `findings` entry of kind `verified_fact` naming the matched row with its evidence refs, and `pattern_influence` all false (section 15.2). It carries no review reason of its own — `escalate_to_human` is mapped by the orchestrator.

**Tests first:** `test_baseline_table.py` — one test per row B1-B9, each with the situation that matches it and one that does not. Plus `test_baseline_matches_scenarios.py`: for every scenario in `eval-scenarios.yaml` whose `baseline:` is an action or `same`, the baseline produces it. Read the YAML at test time — this is what keeps the catalogue and the code honest, and it is the check the Stage A4 review had to do by hand.

**Acceptance:** `pytest tests/unit/packs/test_baseline.py -q`.

**Size:** M. **Model:** Sonnet, effort medium. **Opus review:** yes — it is the fallback that decides cases when the model is down, and the bar the AI is measured against.

**Watch:** B3 (credit) sits **above** B5 (the judgement gate) on purpose. A code-proven credit needs no judgement, so it fires even when the round carries a reply or an image. Reordering these two silently changes what the fallback does on roughly half the fixtures — `agent-design.md` section 5 explains why, and the scenario test above catches it.

---

## P2-09 — Wire evidence and calculation into the orchestrator

**Files:** `src/dispute_agent/kernel/orchestrator/orchestrator.py` (the `gather_evidence` and `assess` steps).

**Implements:** `orchestrator.md` section 3, `transitions.yaml` (`steps`).

**Build:** connect P2-06 and P2-07 to the `GATHERING_EVIDENCE` and `CALCULATING` steps, with the Phase 1 fakes still in place for channels. `assess` escalates before INVESTIGATING when a required item FAILED with `module_unavailable:<m>` (`orchestrator.md`), and produces `assessment_ineligible` (T05) when eligibility is disproven by the evidence (scenario S32).

**Tests first:** `tests/e2e/test_phase2_run.py` — the kurta fixture runs `RECEIVED -> GATHERING_EVIDENCE -> CALCULATING -> INVESTIGATING` with a complete assessment; scenarios S05b, S32 and S46 reach their expected states.

**Acceptance:** `pytest tests/e2e/test_phase2_run.py -q`.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P2-10 — Calculator property tests

**Files:** `tests/unit/packs/test_calculator_properties.py`.

**Implements:** `CLAUDE.md` section 8, the phase prompt's technical requirements.

**Build:** hypothesis over dimensions and weights. Properties: chargeable weight is **monotonic** in each dimension (growing a box never lowers the charge); volumetric weight is never negative; rounding is **stable** (computing twice gives the same answer, and rounding an already-rounded value changes nothing).

**Tests first:** the tests are the deliverable.

**Acceptance:** `pytest tests/unit/packs/test_calculator_properties.py -q` with at least 200 examples per property.

**Size:** M. **Model:** Sonnet, effort medium.

---

## P2-11 — ADR: public facts, assumptions and why every figure is configuration

**Files:** `docs/adr/00NN-weight-pack-figures.md`.

**Implements:** `CLAUDE.md` section 12, D2, D10.

**Build:** state which facts come from public documentation (the volumetric divisor convention), which are assumptions (zones, slab rates, thresholds), and why every one is configuration rather than code. This ADR is what lets the project say publicly that it models a public workflow with illustrative numbers and is not any company's tariff (D2, and the `claims_not_to_make` list in `release-gates.yaml`).

**Tests first:** not applicable.

**Acceptance:** ADR written and listed in `docs/PROGRESS.md`.

**Size:** S. **Model:** Sonnet, effort low.

---

## Phase exit

`make verify` green; the kurta fixture reproduces `FLOW.md` section 7a exactly; every scenario `baseline:` value matches the implemented table; the generator is deterministic; `/phase-verify 2`; `/session-handoff`.
