# 0041 — A LangGraph two-pass investigator, off by default and measured head to head

Date: 2026-11-05
Status: accepted as an **experimental engine, off by default** (D60). Results: `docs/evals/2026-11-05-langgraph-vs-single.md`.

## Decision

Add a second investigation engine on **LangGraph** (`langgraph` 1.2.20), switched on with `REASONING_ENGINE=graph`:

    START -> first_pass -> second_pass -> reconcile -> END

- **The two passes.** The same prompt runs through the same guarded `ModelGateway`, asked twice and independently. Each pass is the existing single-call reasoner, with its own checks and one retry.
- **Reconcile** is plain code with no model:
  - **Same action:** keep it, at the lower of the two confidences.
  - **Different actions:** keep the **more cautious** one (CautionRank, ADR 0025), at LOW confidence, so R22 sends a low-confidence credit or uphold to a person.
  - **Second pass failed:** keep the first, at LOW confidence.

The single-call engine stays the default.

## Why

The live score was below the 90% bar, and the question was whether a structured, multi-step approach does better than one call. A self-consistency check is a cheap way to catch an unstable answer: a model that disagrees with itself should not decide.

## How it stays inside the rules

- **I1.** It returns one `Proposal`, and policy judges it as before. It holds no store and no write port.
- **I14.**
  - Every node is a plain Python function. LangGraph does not require LangChain, and no LangChain model is used.
  - Every model call goes through our gateway, so caps, logging and failover apply.
  - LangGraph is imported in one file only (`tests/architecture/test_agent_libraries.py`).
- **Caution only.** A property test over every pair of actions shows the result is never less cautious than either pass.
- **Budget.** Two calls per round, so the engine gets **twice the configured per-case cap**. Doubling (rather than a fixed number) keeps a deliberately tight cap tight; scenario S23b's cap of 2 becomes 4.

Sources, read on 2026-11-05: docs.langchain.com/oss/python/langgraph (overview, Graph API, control flow). The pages state that `StateGraph` takes a TypedDict state and plain-function nodes, that `invoke` takes a `recursion_limit` config, and that "LangGraph does not require LangChain".

## Results (honest)

- **Recorded suite:** 57 of 57 and 12 of 12 gates, the same as the single engine. The comparison exposed and fixed a stand-in model that behaved like a script.
- **Live:** inconclusive.
  - Same-day runs scored 58% of 45 for graph and 24% of 45 for single, but the single engine scored 62% of 45 the day before with nothing changed.
  - The 45 scenarios rest on 13 distinct disputes, and one dispute decides 29 scenarios. One coin-flip moves the score by tens of points.
  - No claim that the graph engine is better is made.

## Consequences

- The engine stays **off by default**. Turning it on would double model cost per round for an improvement that is not shown.
- **The comparison's real finding concerns the evaluation itself.** Live accuracy should be scored per distinct dispute, over repeated runs. This is now in BACKLOG, and the README states how many independent decisions its live score rests on.
- `scripts/eval.py --live` and `make eval` both honour `REASONING_ENGINE`, so the comparison can be rerun at any time.
