# Single-call vs LangGraph two-pass investigator, 2026-11-05

**Verdict: inconclusive. The test cannot separate the two engines, and it showed that the live evaluation itself measures far fewer independent decisions than its "of 45" suggests.**

Owner-approved cap: USD 0.50, shared with ADR 0039. This comparison cost **USD 0.207** (single 0.064, graph 0.143), about USD 0.21 in total with the Explainer's check.

## Recorded suite (free, deterministic)

| Engine | Scenarios passed | Release gates |
|---|---|---|
| single call | 57 of 57 | 12 of 12 |
| LangGraph two-pass (with its call cap doubled) | 57 of 57 | 12 of 12 |

The first graph runs failed for two reasons, and both were fixed:

1. **The stand-in model was a script, not a model.** It handed out recorded answers in order, so a second pass received the *next* round's answer and scored 15 of 57. It now returns the same answer to the same question, as a model would. The single engine's result is unchanged (57 of 57).
2. **Two passes cost twice the calls.** Three cases hit the 6-call safety cap and correctly went to a person. The graph engine now gets **twice the configured cap**: 12 by default, and a deliberately tight cap of 2 in scenario S23b becomes 4, so that budget test still passes.

On recorded answers the two engines agree by construction, because a recorded answer never disagrees with itself. The live run is the only place they can differ.

## Live (Microsoft Foundry, gpt-5-mini; first decision per scenario)

| Run | Correct first step | Regressions vs rules | Cost |
|---|---|---|---|
| single call, 2026-11-04 | 62% of 45 | 7 | USD 0.07 |
| single call, 2026-11-05 | 24% of 45 | 10 | USD 0.064 |
| LangGraph two-pass, 2026-11-05 | 58% of 45 | 10 | USD 0.143 |
| rules only (reference) | 48% of 45 | – | free |

## Why this is inconclusive

- The 45 scored scenarios rest on **13 distinct disputes** (fixtures), and the harness reuses each dispute's first decision for every scenario built on it.
- One dispute, `kurta_box_larger`, sits behind **29 of the 57 scenarios**. On 2026-11-05 the single engine chose `request_remeasurement` for it, and the graph engine chose the expected `request_seller_evidence`. That one choice accounts for most of the 24% vs 58% gap, and for most of the 62% vs 24% swing between two identical single-engine runs a day apart.
- The run-to-run noise on the *same* engine (62% vs 24%) is larger than the gap between engines, so no claim that either engine is better is supported.

## What would settle it

Score per distinct dispute, not per scenario, and run each dispute several times, for example 13 disputes × 5 runs × 2 engines. That is roughly USD 0.5 to 0.7 at current costs, and needs the owner's approval. Report each dispute's agreement rate across runs, which is also a direct measure of how stable the model's judgement is.

## What did hold

- **Safety:** on every live answer, the graph engine's choice was never less cautious than either of its passes. A property test checks every action pair.
- **Cost:** the graph engine costs about 2.2× the single engine, as designed.
