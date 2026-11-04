"""The injection corpus now lives with the eval module (the live red-team run reads it too)."""

from dispute_agent.modules.evals.injection_corpus import CORPUS, WITHDRAWAL_ITEM

__all__ = ["CORPUS", "WITHDRAWAL_ITEM"]
