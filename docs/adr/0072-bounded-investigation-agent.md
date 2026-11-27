# 0072 — A bounded investigation agent on a deterministic core

Date: 2026-11-27
Status: **built, not activated** (owner, 2026-11-27, "Approved Product and Architecture Direction"). Kernel change: the briefing carries prior rounds and reviewer notes. Builds on ADR 0071.

## The goal (the owner's, as evaluation targets, not results)

- **Fewer contacts:** cut unnecessary seller-evidence and hub-remeasurement requests by at least 25% together, reported separately.
- **Accuracy:** keep or improve decision accuracy against today's workflow.
- **Cost:** no increase in AI cost per correctly resolved ticket.
- **Safety:** zero financial-safety, privacy or approval violations.
- **No complexity for its own sake:** add no agentic complexity only to look autonomous.

## Decision

**Code keeps doing the basics.** Mandatory shipment and billing evidence, the scan history, every calculation, proven errors, the compact authoritative history comparison, policy, the allowed actions, and now case history: earlier rounds' decisions, structured only, and reviewers' rejection notes.

**The model investigates only when that is not enough.**
- **Engine:** `investigator` (`modules/reasoning/llm/investigator.py`, prompt `case_investigator_agent/v4`).
- **How it works:** each turn the model calls one of the five tools of ADR 0071 with its own arguments, or decides.
- **Prompt:** principles, not situation-to-tool routes.
- **Tools available:** only those five; `open_evidence` and `check_draft` are gone, and code checks every decision, giving one retry.

**Budgets are in code, not in the prompt.**
- **Tool calls:** at most 5 per round; zero is normal.
- **Paid checks:** at most 1 image check per round.
- **No repeats:** a repeated query in a round is not run; the model is pointed at the earlier result.
- **Reuse across rounds:** results that cannot change within a case (history before the parcel, scanner statistics, policy, the photo) are reused in later rounds, not fetched again.
- **No draft on investigation turns:** the full proposal is written only on "decide".
- **Case cap:** the investigator gets twice the per-case model-call cap, as the graph engine does. Exhausting it still sends the case to a person.

**Cost and observability.**
- **Prompt caching:** the Anthropic route marks the stable system prompt (over 512 tokens) for the prompt cache.
- **Cache accounting:** cache writes and reads are recorded and priced at 1.25× and 0.1× of input, so the spending cap stays true.
- **Per-turn log:** every turn's tokens, cache use, latency, cost and tool call go to `data/investigation_turns.jsonl`, with no outside text.

**`search_past_shipments` is a real search.**
- **Filters:** product, packaging, quantity, trusted-only, dates, hub, scanner, limit.
- **Results:** compact records, code-computed statistics, sample sizes and the valid filter values.
- **Labels:** each result is labelled `authoritative_basis` or `supplementary`.
- **What supplementary results can do:** shape the investigation, a request or a recommendation. They never establish a billing error or authorise a credit.
- **No verdict:** the authoritative comparison (`history.compare`) is unchanged and stays in the briefing, where the fallback rules read it.

**Privacy.**
- **Own seller only:** individual records are only ever the case's own seller's, set by code.
- **Aggregates:** cross-seller figures (scanner and hub statistics, other sellers' dispute outcomes) need at least 5 readings or cases from at least 3 sellers, else `insufficient_data`.
- **No differencing:** scanner windows are fixed, anchored on the parcel and never chosen by the agent, so two queries cannot be subtracted.
- **Never shown:** planted labels and the generator manifest never reach the agent.

**Storage stays SQLite.** The tools read `ShipmentRecords` and `CaseOutcomes` interfaces, so PostgreSQL can replace the store without touching a tool or the agent.

## Synthetic data

**Generator:** `scripts/generate_sellers.py` (seeded, deterministic, committed) adds 7 sellers and 365 shipments beside the 3 approved sellers (84 shipments), 449 in all.
- **Shape:** 12 months, shared hubs and scanners.
- **Planted variations:** a scanner drift, a hub drift, a packaging change, a product change, two packagings at once, multi-item orders, corrected and disputed readings, thin histories, and normal variation.
- **Manifest:** `evals/investigation/generated_manifest.yaml`.

**Frozen environment:** `SELLER_CATALOGUE=approved` loads only the approved sellers.
- **Snapshot:** a snapshot test holds all 84 approved comparisons. They are identical with or without the generated sellers, because a comparison reads only its own seller.
- **Approved-data tests:** the tests that assert approved data run in frozen mode.

## Console

**Added shipments:** they can belong to a demo seller, with their product, packaging, quantity, hub, scanner and date.
- **Comparison:** they get the same comparison against that seller's history.
- **Never history:** they never become anyone's history.

**Seller view:** disputes and added shipments first, then 25 per page, with search and a filter. Only disputes and added shipments can be raised.

## Evaluation

**Datasets:** `evals/investigation/cases_v1.yaml` has 20 cases. They are kept separate from the end-to-end suite and the 18-point Decision Quality set, and are to be mapped onto the 60-scenario candidate set when it is provided.

**Coverage:** zero-tool, single-tool, refined-query, multi-tool, conflicting and unresolvable cases.

**Grading:** each case is graded on its outcome, the information consulted (not the tool order), unnecessary hub or seller contacts, over-investigation, safety, cost and latency.

**Scripted tests:** `tests/evals/investigation` prove the plumbing and that the data supports each case. They say nothing about decision quality.

**Paid comparison:** `scripts/investigation_eval.py` compares `agent` (today), `enriched` (code runs the look-ups, one call) and `investigator` on the same cases. It is paid and needs the owner's approval.

## Not done here

- **Prompt not active:** v4 stays inactive until the paid comparison clears it.
- **Reply reader v2 not wired:** its new fields need the reading model extended.
- **[Decision for the owner]** v4 lets the agent uphold without asking the hub when at least 5 trusted shipments in the matching packaging measure like the parcel and the scanner and hub look normal. This is the main lever for the contact target. A person still approves the uphold email.
- **[Assumption]** The thresholds (5 readings, 3 sellers) and the 1.25× "bigger than declared" line are defaults.
- **Photo case on the paid route:** the photo case has no image check on the Anthropic route.
