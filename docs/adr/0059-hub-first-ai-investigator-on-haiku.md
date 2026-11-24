# 0059 — Hub first: the AI investigator, on Claude Haiku, switched on

Date: 2026-11-24
Status: accepted. The owner asked for it.

## Decision

**Hub first.** When the charge follows the hub's measurement and that measurement is far larger than the seller declared, with no error that code can prove, the AI investigator asks the hub to re-measure (Jira) before asking the seller anything. Only the hub can check its own scanner, and sellers rarely photograph their parcels.
- **The hub's re-measure proves an error** (`remeasurement_lower`): code attaches the credit, and a person approves it.
- **The hub finds no fault, or re-measures and nothing changes:** the charge is upheld, and the seller is told why.
- **Sellers are not asked for photos.** If a seller must be asked, it is for what they have on record, such as the box size used at dispatch.

**The prompt** is `case_investigator_agent/v2` (v1 kept for replay). It sets an order of precedence, checked top to bottom:
1. Any manipulation sign goes to a person.
2. Two or more proven errors go to a person.
3. Exactly one proven error gets a credit.
4. Otherwise, hub first.

It also states the output envelope (`step`, `argument`, `draft`) explicitly. Haiku follows the examples, and unlike the Azure route nothing enforces the schema for it.

**Switched on, hosted.**
- `model_gateway: anthropic_direct` is now a primary route (`app.build_model_gateway`). It is held to the Haiku route's own cap (`LLM_SECONDARY_SPEND_CAP_USD`), as the reply reader is.
- `reasoning: llm` is built by the runtime (`app._runtime_reasoner`). Until now only scripts could build it.
- `REASONING_ENGINE` now defaults to `agent`, and the startup prompt check follows the chosen engine.

**The rules table** (the AI's fallback and the bar it is measured against, ADR 0024) changes in one row, B6a:
- When the hub has answered (`remeasured` or `no_fault_found`) and code finds no error, the charge is upheld.
- B6a sits above B6 on purpose. OPS_TIMEOUT stays armed after a finding until a new plan (T31), so "remeasurement outstanding" is still true in the finding's own round. Without B6a, the answer would be met with `wait`, and the AI made exactly that mistake before the prompt said so.
- B7 still asks the seller first. Making it hub first would move the measuring stick and the 40-odd tests built on it.

## Evidence

- `scripts/agent_check.py --route anthropic_direct` decided all 11 distinct disputes validly and matched the hub-first design on each, for USD 0.026.
- New scenarios S60 (a scanner fault proven by the hub's re-measure: credit, two approvals) and S61 (the hub finds no fault: upheld) ran end to end with live Haiku and fake Jira and email. Each passed twice, at about USD 0.003 per run. Offline, both run on recorded answers in the scenario suite.

## Consequences

- Each dispute on the live site now spends a few Haiku calls, about USD 0.002–0.004.
- If the model fails or returns an unusable answer, the case goes to a person (`invalid_model_output`). It never falls through to a decision.
- Not done: telling the seller "we have asked the hub to re-measure" (it needs a new message kind). The seller page's "Your tickets" shows the step meanwhile.
- The live check scores "expected" against the catalogue's first scenarios, which still describe the seller-first path. Accuracy is read against this ADR, not that column.
