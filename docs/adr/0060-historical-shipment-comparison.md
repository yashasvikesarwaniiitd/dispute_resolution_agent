# 0060 — Historical shipment comparison, three demo sellers

Date: 2026-11-24
Status: accepted. The owner approved the plan, the dataset, the method and the implementation, each separately; the decision log is in the session's plan.

## Decision

**Three synthetic demo sellers** live in `config/sellers/*.yaml`, which is the source of truth:
- **NorthCart Essentials** (SELL-0101), a general store;
- **CaseKraft Accessories** (SELL-0102), mobile accessories;
- **ThreadLane Apparel** (SELL-0103), clothing.

There are 11 products, 10 packaging profiles, 73 past shipments and 11 disputes. Every charge follows the rate card except the two deliberate billing errors. **Every dispute gives the seller a real reason to dispute:** a bill above what they declared, or a charge taken twice. The first version had six disputes whose bill equalled the declaration; no real seller raises those, so they were made realistic on 2026-11-25 with each case's purpose kept, and CaseKraft's protector, whose any honest measurement is one slab, became a past shipment. The data is YAML only; Supabase is deferred. `catalogue.py` validates the files on load and serves every shipment to the agent as a named fixture is served.

**The comparison** is an evidence item computed in code during every evidence round: `shipment.comparable_history`, from `packs/weight_dispute/history.py`, which is pure. The AI and the rules read the same result.
- **The reference pool:** the same seller, SKU, packaging and quantity; measured before this parcel; not this parcel; `trusted` only.
- **`trusted` means eligible as a reference, not proven correct.** The original readings of disputed and corrected shipments are excluded and counted.
- **Second guards:** sides are sorted so rotation doesn't matter, and outliers (outside 0.67–1.5 × the median) are kept out of the range.
- **Verdicts:** `missing_evidence`, `not_comparable`, `insufficient_history` (fewer than 5), `anomalous` (above 1.25 × the median **and** above the maximum), otherwise `normal`.
- **Flags:** `declared_below_history` (below 0.8 × the median) and `packaging_consistent` (the measured volume is 0.75–1.25 × the nominal volume of the packaging recorded at booking).
- **No percentages and no confidence scores.**

**Rules (after B5 and B6a, so a reply, an image or free-text notes reach a person first):**
- **B4b:** `anomalous` and the hub hasn't answered → `request_remeasurement`, worded as a scanner audit with no figures in the question.
- **B4c:** `normal` with at least 5 references, `declared_below_history` and `packaging_consistent` → `uphold_charge`. History supports the hub's figure and never proves the seller wrong. The upheld explanation still needs a reviewer's approval, and the seller can reply with evidence within the reopen window.
- **B5's code is unchanged.** The new structured key is not in what B5 checks, so having history no longer escalates; the old free-text `shipment.history` still does.
- **No history verdict ever proposes a credit.** B3 (one error proven by code) and B4 (two errors go to a person) still come first.

**Prompt `case_investigator_agent/v3`** adds the same reading of the comparison. It was activated on 2026-11-24 after its live Haiku check: 17 of 17 distinct disputes decided as designed with no invalid answers, and S62-S67 passing end to end. `agentic.VERSION` is now v3; v1 and v2 are kept for replay.

**The hub's answer is never in the data.** Scenario F's correction (tray mode on SCN-BLR-03-2 since maintenance on 18 Nov; re-measured 19 × 11 × 2 cm, 72 g) is in the findings bank (`tray_mode_corrected`). Only the scenario's `ops_finding` step supplies it, so round 1 sees `ops.finding` as missing. On the live site a finding comes only from a real Jira comment.

**UI:**
- **Raise a ticket:**
  - **A "Viewing as (demo seller)" selector:** labelled as not a login.
  - **Only the seller's 4 disputed shipments are listed,** with no filter. The 24 past shipments stay in the data, where the agent's comparison reads them.
  - **No comparison panel.** The comparison is evidence the agent weighs after a dispute is raised; showing it first would preview the agent's reasoning.
  - **The verdict comes before the working,** and a shipment debited twice reads as a billing error.
  - **SELL-0001's five examples are removed.**
- **Review:** the seller's name on each card, and the "Compared with this seller's past shipments" panel.
- **Raising a dispute** uses the seller's own `+tag` address from `TEST_SELLER_EMAIL`. Without it the button explains what to add, and no other seller's address is used.

## Scenarios

The table shows the rules alone, with the AI off. The recorded AI answers reach the same outcomes.

| | Fixture | Rules | Final |
|---|---|---|---|
| S62 A | `ck_tray_anomaly` | B4b scanner audit | waiting on the hub |
| S63 F | `ck_tray_confirmed` | B4b, then B3 (`remeasurement_lower`, INR 152) | credited after two approvals |
| S64 B | `tl_tee_carton` | B8: `not_comparable` favours no side; the hub's carton, 4.6% taller than declared, is 6 slabs (INR 228) against the declared 5 (INR 190) | upheld |
| S65 C | `nc_lamp_duplicate` | B3 `duplicate_charge` | credited after approval |
| S66 D | `nc_kettle_new` | B8 (2 past kettles: too little to compare); 4.4% taller than declared, 6 slabs against 5 | upheld |
| S67 E | `nc_lamp_underdeclared` | B4c, consistent with 10 trusted lamps | upheld after approval |

## Two fixes found by the golden acceptance test (owner-approved)

**1. A kernel change: the hub sees the approved audit wording.** `investigate._payload` used to write the same remeasurement request every time, and dropped the proposal's question. It now asks the pack (`WeightDisputePack.remeasurement_brief`) and receives either an approved question or None.
- **What passes:** only an exact match from `baseline.APPROVED_HUB_QUESTIONS` (today: B4b's scanner audit). The issue then opens "Please check shipment …", followed by that question.
- **Everything else:** the standard request, unchanged. That covers a model's own words, untrusted text, and an injected instruction. Nothing a model writes reaches Jira verbatim.
- **Compatibility:** the kernel calls the pack method optionally (`getattr`), so packs and test fakes without it behave exactly as before.

**2. The AI sees the comparison's values.** The model's briefing shortened any trusted value over 700 characters to its key names, which hid the verdict of a full comparison. `briefing_view._comparison_view` now gives this key a compact view:
- **Kept:** the verdict, the count, current, typical, range, flags, exclusions, `differs_by`, and the reference ids.
- **Shortened if needed:** only the id list, from the end ("+N more").
- **Dropped:** the derived ratios and the explanatory text.
- **Size:** it stays within 700 characters (SHP-102026: 565).
- **Contents:** nothing the stored value doesn't hold, so no outcome, label, other seller or hub finding.
- **Prompt v3** tells the model the approved question verbatim.

## Consequences and limits

- **"Add a shipment" is hidden.** It adds to the old single demo seller (ADR 0056), which this page no longer shows, and attaching added shipments to a catalogue seller needs a change to its store.
- **`measurement_status` is static in the YAML.** A live case opened on a past shipment doesn't flip it to `disputed`.
- **The three `+tag` addresses** must be added to `TEST_SELLER_EMAIL` (`.env` and Streamlit Secrets) before disputes can be raised for the demo sellers.
- **Two test-harness tables learned the new fixtures:** the finding kind for `tray_mode_corrected`, and the credit reasons for the two billing errors. The scenario world lets a catalogue seller keep their own synthetic address; any other domain is still refused (S27).
