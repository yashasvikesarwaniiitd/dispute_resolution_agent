# History search and synthetic data: design for review

Status: **proposal, not implemented** (2026-11-27). Covers Phase 1 (tool) and Phase 2 (data) together. No paid evaluation runs. The deterministic comparison (`packs/weight_dispute/history.compare`) and the rules fallback are not changed.

## 0. What exists today

| Area | Today |
|---|---|
| Demo sellers | 3 hand-approved files, `config/sellers/*.yaml`: 84 shipments, Jul–Nov 2026, 6 scanners on 3 hubs |
| Authoritative comparison | `compare()`: same seller, same product, same packaging, same quantity, measured earlier, trusted only, at least 5 references. Fed upfront to the agent; read by the rules fallback (B4b) |
| Depends on the 3 files | Scenarios S62–S67, the 18-point decision-quality golden set, `test_seller_catalogue.py`, the seller page, the ledger seed |
| Console-added shipments | Own product code per shipment (`SKU-<ref>`), placeholder hub and scanner, no date, no quantity, packaging type only: **never matches any history** |
| Generator | `generator.py` has a seeded world builder (synthetic-data.md) the catalogue does not use |

## 1. Tool design: `search_past_shipments`

### 1.1 Arguments (all optional)

| Argument | Type | Default | Notes |
|---|---|---|---|
| `sku_id` | one of this seller's products | this parcel's product | |
| `packaging_id` | one of this seller's packagings, or `"any"` | `"any"` | |
| `quantity` | integer 1–50, or `"any"` | `"any"` | |
| `measurement_status` | `"trusted"` or `"any"` | `"trusted"` | `"any"` adds disputed and corrected records |
| `date_from`, `date_to` | ISO date | none | Always capped: only shipments measured **before** this parcel (never the future) |
| `hub_id`, `scanner_id` | from this seller's own shipments | none | Filters this seller's records only |
| `limit` | 1–20 | 10 | Records returned; statistics cover all matches |

There is no seller argument: the seller is always the case's own, taken from the case, never from the agent.

### 1.2 Result (compact, figures as strings, computed in code)

```json
{
  "query": {"sku_id": "TL-TEE-M", "packaging_id": "any", "quantity": "any", "measurement_status": "trusted", "date_from": null, "date_to": null, "hub_id": null, "scanner_id": null},
  "basis": "supplementary",
  "differs_from_authoritative": ["packaging: any", "quantity: any"],
  "matched": 23, "shown": 10,
  "stats": {
    "sample_size": 23,
    "median_measured_cm": "21 x 13 x 4", "median_grams": "260",
    "volume_cm3_range": ["980", "1240"],
    "this_parcel_volume_to_median": "2.84",
    "share_measured_over_1_25x_declared": "0.04",
    "by_packaging": {"PKG-TL-POLY-M": 19, "PKG-TL-CARTON-M": 4},
    "by_quantity": {"1": 20, "3": 3}
  },
  "records": [
    {"ref": "SHP-...", "measured_on": "2026-10-02", "sku_id": "TL-TEE-M", "packaging_id": "PKG-TL-POLY-M", "quantity": 1,
     "hub_id": "HUB-BOM-02", "scanner_id": "SCN-BOM-02-1", "declared": "21 x 13 x 4 cm, 250 g",
     "measured": "21.2 x 13 x 4.1 cm, 262 g", "measurement_status": "trusted"}
  ],
  "available_filters": {
    "products": [{"sku_id": "TL-TEE-M", "name": "Cotton tee (M)", "records": 61}],
    "packagings": [{"packaging_id": "PKG-TL-POLY-M", "kind": "poly mailer", "records": 70}],
    "quantities": [1, 3], "hubs": ["HUB-BOM-02"], "scanners": ["SCN-BOM-02-1", "SCN-BOM-02-2"],
    "measured_between": ["2025-12-01", "2026-11-19"]
  },
  "notes": ["Fewer than 5 matches: too few for statistics."]
}
```

- **`basis` is "authoritative" only when** the filters equal the authoritative comparison's basis: same product, packaging and quantity as this parcel, trusted only, no date, hub or scanner filter. Then `stats` match what `compare()` uses, but the tool still does not restate the verdict: the verdict is already in the briefing.
- **Every other query is "supplementary".** `differs_from_authoritative` names each difference.
- **No verdict words.** Supplementary statistics never say "normal" or "anomalous", so they cannot be confused with the authoritative verdict.
- **`available_filters`** comes from this seller's shipments measured before this parcel, so the agent can refine its next query with valid values.

### 1.3 Alternative comparison logic

- **Where it lives:** a new function in the tool module (`supplementary_stats`), not in `history.py`. `compare()` is imported nowhere new and edited nowhere.
- **What it reuses:** the pack's pure helpers (`sorted_sides`, `volume`), read-only.
- **What it computes, over the matched set:** sample size, median size and weight, volume range, this parcel's ratio to the median, the share measured over 1.25x declared, and counts by packaging and quantity.
- **Statistics use trusted records only,** even when `measurement_status` is `"any"`: disputed and corrected records are listed, never averaged.
- **Too few matches:** under 5 trusted matches, `stats` holds the sample size only, with a note.
- **Cross-product and cross-packaging statistics are context only.** The prompt states that they are not a baseline, cannot trigger the approved scanner-audit wording, and cannot support an uphold. Those stay tied to the authoritative verdict.

### 1.4 Prompt changes (agent v4)

- **Briefing wording:** split into two named inputs:
  - "scan and tracking history" (this parcel's scan events);
  - "comparison with the seller's past shipments" (the authoritative verdict).
- **No list upfront:** the briefing never carries the full shipment list.
- **When to call the tool:**
  - not to recover the verdict, which is already in the briefing;
  - when individual records, a supplementary view, or a narrower or wider query could change the recommendation or sharpen a request to the hub or seller.
- **Refining:** a later call may refine an earlier one with different arguments; the same arguments are never repeated.
- **No credit from history:** history, authoritative or supplementary, never authorises a credit.
- **Principles, not routes:** the current "situation → tool" list in `investigation_strategy` reads like a set of fixed routes. It becomes principles: what each tool can tell you, and choosing the one whose answer would change the decision.

### 1.5 `get_scanner_statistics` under 10 sellers

It stays aggregate across sellers, which is what makes it useful, with two privacy guards:
- counts only, never refs or seller IDs;
- each statistic is suppressed when fewer than 5 readings back it.

A new hub-level view (all scanners at the hub against the hub's own history) separates a scanner fault from a hub fault. Either as a `level: "scanner" | "hub"` argument, or included in the same answer.

## 2. Synthetic data generation

### 2.1 Shape

| | Target |
|---|---|
| Sellers | 10: the 3 approved, unchanged, plus 7 generated |
| Shipments | about 2,000: 84 approved plus about 1,920 generated (about 275 per new seller) |
| History | 12 months, 2025-12-01 to 2026-11-25 (generated sellers) |
| Products | 3–8 per new seller, each with 1–3 packaging options over time |
| Hubs and scanners | the 3 existing hubs plus 3 new ones; 2–3 scanners each (about 15 scanners) |
| Disputes | 3–6 per new seller, each tied to a named variation (section 2.3) |

### 2.2 How it is built

- **One command:** `make sellers` runs `scripts/generate_sellers.py`.
  - The seed is a constant, with one random stream per family (sellers, products, packaging, shipments, noise, variations). Adding a family never changes the others, as in synthetic-data.md section 1.
  - It writes `config/sellers/generated/<seller>.yaml` in the **existing `SellerFile` format**, so the catalogue loads them with no new parser. It also writes a `manifest.yaml` that names every planted variation: seller, product, period, rows and purpose.
- **Committed files, not generated at start-up.** They are reviewable in a diff, the site starts fast, and a test regenerates them and requires byte-identical output.
- **The 3 approved files are untouched.** The catalogue's glob gains `generated/*.yaml`.
- **The physics:**
  - each product has a true size and weight;
  - packaging adds its nominal size and tare;
  - quantity scales weight and, where defined, height;
  - each seller has a declaration habit (accurate for most; some under-declare);
  - measured size is the true size plus scanner noise, Normal(0, 3 mm) per side, clipped to ±10 mm;
  - weight noise is ±2%;
  - bills follow the rate card on the measured size, except planted billing errors.
- **All synthetic.** Invented names; contacts on the test domain (`seller+<tag>@…`).

### 2.3 Planted variations (each in the manifest)

| Variation | How it is planted | What it tests |
|---|---|---|
| Packaging change over time | A product moves from packaging A to B on a fixed date | Date filter; "not comparable" right after the change |
| Several packagings at once | A product ships 70/30 in two packagings | Packaging filter; supplementary vs authoritative |
| Single and multi-item | Quantities 1–5 on some products | Quantity filter |
| Corrected measurements | Rows marked `corrected` after a planted mis-read | Status filter; scanner statistics |
| Scanner anomaly | One scanner over-reads by 15–30% for 6 weeks | Scanner statistics; scanner filter |
| Hub anomaly | Every scanner at one hub over-reads for 3 weeks | Hub vs scanner; hub filter |
| Recent product or packaging change | A change 2 weeks before a dispute | Insufficient authoritative history; supplementary context |
| Insufficient history | New products with fewer than 5 shipments | "insufficient_history"; escalation |
| Mixed trusted and disputed | Disputed rows spread through the history | Status filter |
| Normal variation | Everything else | False-positive control |

### 2.4 Console-added shipments

- **New form flow:**
  1. Pick a seller.
  2. Pick one of that seller's products; packaging defaults to the product's current one and can be changed.
  3. Set the quantity.
  4. Pick hub and scanner from the catalogue.
  5. Set the measured date (default today, never in the future).

  Declared size and weight prefill from product and packaging, and can be edited.
- **The stored row gains:** `seller_id`, `sku_id`, `packaging_id`, `quantity`, `hub_id`, `scanner_id`, `measured_at`. Old rows with no such fields keep today's behaviour (no history).
- **The comparison for an added shipment** is computed when it is served, by the **same unchanged `compare()`**, against that seller's catalogue history.
- **Added shipments are disputes, never history.** They never enter another shipment's comparison or the scanner statistics, so a test parcel cannot skew the baseline.

## 3. Privacy controls

| Rule | Where it is enforced |
|---|---|
| The seller is always the case's own; there is no seller argument | `ToolContext` facts, set by code (ADR 0071) |
| History records are this seller's only | Tool filters on the case's seller before any other filter; test asserts no other `seller_id` or ref in any output |
| Cross-seller data is counts only, at least 5 readings | Scanner and hub statistics; suppression test |
| No future records | `measured_at < this parcel`, applied before filters |
| Hidden fields never returned | `demo_label`, `role` (history or dispute) and any outcome are absent from the record shape; a test asserts the key set |
| Past outcomes only via `search_past_disputes` | Own disputes listed, others counted (unchanged) |
| Synthetic only | Generator header and manifest; test domain contacts; no real names |

## 4. Evaluation design

**Base:** `docs/spec/eval-scenarios.yaml`, 67 scenarios. Taken as the candidate golden set; S62–S67 use the demo sellers. Those stay unchanged. A new family `S70+` uses generated sellers, with about 4 cases per category, about 20 cases:

| Category | Example cases |
|---|---|
| No tool needed | One proven billing error; manipulation in the note; a clear authoritative "normal" uphold after a hub answer |
| One tool enough | Scanner anomaly: scanner statistics show the scanner over-reading, then remeasure with audit |
| Refined query | First query (same product, any packaging) shows a packaging change; refined by date after the change; the request to the seller targets the new packaging |
| Several tools combined | Hub anomaly: scanner statistics look normal, the hub view and past disputes on the hub show a hub-wide problem, then escalate or audit |
| Investigation cannot resolve it | Insufficient history, two packagings, a hedged seller reply: escalate to a person |

**Each case states:**
- the expected final action (or an acceptable set);
- `must_consult` (information the agent must have looked at), never an exact tool order;
- `must_not` (for example, a credit, or a photo check when no photo exists);
- a step budget.

**Trajectories are graded on outcome and efficiency, not on matching a script.**

**Metrics:**
- correct-action rate;
- unsafe-action rate (credit without a proven error: gate 0);
- escalation rate;
- tool calls and steps per case;
- unnecessary calls in the "no tool" category;
- refinement rate in the "refined" category;
- cost per case.

**Comparisons:** v3 (no tools), v4 with the compact tool, and v4 with the expanded tool.
- **Now:** scripted models only, which prove the plumbing deterministically.
- **Later, live runs (Haiku):** only after your approval.

## 5. Regression risks

| Risk | Mitigation |
|---|---|
| Approved comparisons change | 3 files untouched; a snapshot test of all 84 current comparison values must stay identical |
| `test_seller_catalogue` counts (3 sellers, 73 past, 11 disputes) | Scoped to the 3 approved seller IDs; new tests for the generated set |
| Scanner statistics shown for existing demo disputes change, if new sellers share their hubs | Not an approved outcome (new tool), but visible in demos. The generator keeps `SCN-BLR-03-2` an over-reader so the SHP-102026 story holds. **Decision needed: shared or separate hubs** |
| Start-up time (ledger seeds every shipment; the catalogue runs `compare()` for every row) | Measure. Expected well under a second for about 275 rows per seller; seed lazily if not |
| Seller page and Desk seller view built for 28 rows | Show disputes plus the most recent 25, with a "show more" |
| 10 sellers in the switcher; 7 have no allowlisted test address | The page already says "no test address yet". Raising disputes as them needs your `TEST_SELLER_EMAIL` change |
| Added-shipments store gains columns | Read old rows with defaults (no history); the hosted store resets anyway |
| Scenario suite, decision-quality golden set, `agent_check` | Use only the approved sellers or named fixtures: unchanged, and re-run to prove it |
| Photo tool on generated sellers | No photos: "no_photo". Unchanged; noted in the manifest |
| Rules fallback | Reads only the authoritative comparison: unchanged |

## 6. Decisions needed before implementation

1. **Hubs:** should new sellers share the 3 existing hubs (richer scanner statistics; SHP-102026's numbers change while staying consistent) or use only new hubs (existing numbers unchanged; thinner statistics)? Recommendation: **share**.
2. **Committed generated files** (recommended), or generation at start-up?
3. **Seller page** shows disputes plus the 25 most recent shipments? (Recommended.)
4. **Rewrite `investigation_strategy` as principles** (recommended, per "no hard-coded trajectories")?
5. **Thresholds:** 5 trusted matches for statistics, 5 readings for cross-seller counts. Agree?
