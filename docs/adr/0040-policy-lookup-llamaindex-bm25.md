# 0040 — Policy lookup with LlamaIndex BM25 (local, no embeddings)

Date: 2026-11-05
Status: accepted (D60).

## Decision

The Case Explainer's `policy_lookup` tool searches the project's own policy text with a **LlamaIndex BM25 retriever** (`llama-index-core` 0.14.25, `llama-index-retrievers-bm25` 0.8.0).

- **Indexed:** `docs/spec/policy-rules.md`, `docs/spec/escalation-guide.md`, `docs/FLOW.md` and every decision record in `docs/adr/`.
- **Chunking:** 220 tokens with a 30-token overlap, about 245 passages in all.
- **Never indexed:** case data and anything a seller wrote.

## Why keyword search, not embeddings

- **Free and private.** No embedding model and no network call; the index is built in memory when the process starts. A test blocks the network and the lookup still works.
- **Better at our vocabulary.** The questions a reviewer brings are literal tokens, such as `R16`, `policy_escalated`, `module_unavailable` or `AWAITING_OPS`. Keyword scoring (BM25) matches those exactly. A meaning-based search could return "something similar" for a rule id, which is the wrong answer.
- **Small corpus.** A few hundred passages need no vector store.

Sources (read on 2026-11-05): developers.llamaindex.ai, *BM25 Retriever* example and the `llama_index.retrievers.bm25` API reference. `BM25Retriever.from_defaults(nodes=..., similarity_top_k=...)` uses PyStemmer and `bm25s`, with no LLM or embedding model.

## Consequences

- LlamaIndex is imported in one file only (`modules/reasoning/explainer/policy_index.py`), enforced by `tests/architecture/test_agent_libraries.py`.
- A passage is capped at 900 characters, and at most 5 are returned per query.
- A new decision record is searchable as soon as the process restarts.
