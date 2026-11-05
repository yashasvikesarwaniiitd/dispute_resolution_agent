"""Policy lookup for the Case Explainer, with LlamaIndex BM25 (ADR 0040).

The explainer answers "why did this case go to a person?". The case's own record says
*which* rule or reason fired (`R16`, `policy_escalated`, `module_unavailable:ledger`); this
index finds *what that rule says*, in the project's own words, so the answer can quote it.

Keyword retrieval (BM25), not embeddings, on purpose:
- **free and local**: no embedding model, no network call, nothing leaves the machine;
- **exact on the vocabulary that matters**: rule ids, reason codes and state names are
  literal tokens, which keyword search matches better than meaning-based search.

Indexed: `docs/spec/policy-rules.md` (the rules R01-R25 and review reasons),
`docs/spec/escalation-guide.md`, `docs/FLOW.md` and every decision record in `docs/adr/`.
Only project documents - never case data, and nothing a seller wrote.

LlamaIndex is imported here and only here; the kernel never sees it (import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

#: Small chunks: a rule row or a short section, so a passage quoted to a reviewer is focused.
CHUNK_SIZE = 220
CHUNK_OVERLAP = 30
DEFAULT_TOP_K = 3
MAX_PASSAGE_CHARS = 900


@dataclass(frozen=True)
class Passage:
    source: str
    text: str
    score: float


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "docs" / "spec" / "policy-rules.md").is_file():
            return parent
    raise FileNotFoundError("cannot find docs/spec/policy-rules.md from " + str(here))


def sources(root: Path) -> list[Path]:
    docs = root / "docs"
    found = [
        docs / "spec" / "policy-rules.md",
        docs / "spec" / "escalation-guide.md",
        docs / "FLOW.md",
        *sorted((docs / "adr").glob("0*.md")),
    ]
    return [p for p in found if p.is_file()]


class PolicyIndex:
    """Builds once per process; `search` is a pure, local keyword lookup."""

    def __init__(self, root: Path | None = None) -> None:
        from llama_index.core import Document
        from llama_index.core.node_parser import SentenceSplitter
        from llama_index.retrievers.bm25 import BM25Retriever

        base = root or project_root()
        documents = [
            Document(
                text=path.read_text(encoding="utf-8"),
                metadata={"source": str(path.relative_to(base)).replace("\\", "/")},
            )
            for path in sources(base)
        ]
        splitter = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
        self._nodes = splitter.get_nodes_from_documents(documents)
        self._retriever_class = BM25Retriever
        self._retrievers: dict[int, Any] = {}

    @property
    def size(self) -> int:
        return len(self._nodes)

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[Passage]:
        query = (query or "").strip()
        if not query:
            return []
        top_k = max(1, min(top_k, 5))
        retriever = self._retrievers.get(top_k)
        if retriever is None:
            retriever = self._retriever_class.from_defaults(
                nodes=self._nodes, similarity_top_k=top_k
            )
            self._retrievers[top_k] = retriever
        found = []
        for hit in retriever.retrieve(query):
            node = hit.node
            text = str(node.get_content()).strip()
            found.append(
                Passage(
                    source=str(node.metadata.get("source", "")),
                    text=text[:MAX_PASSAGE_CHARS],
                    score=float(hit.score or 0.0),
                )
            )
        return found


@lru_cache(maxsize=1)
def default_index() -> PolicyIndex:
    return PolicyIndex()


__all__ = ["Passage", "PolicyIndex", "default_index"]
