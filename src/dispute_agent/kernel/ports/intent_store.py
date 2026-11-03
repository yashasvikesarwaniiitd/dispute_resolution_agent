"""The IntentStore port (kernel-interfaces.md section 6.2).

An intent is what the system wants to do, written down with its exact payload *before*
anyone approves it. The approval row points at it, and the hash on it is what an approval
binds to (I13). Writing the intent first is what makes "the text sent is the text approved"
checkable at all: there has to be a fixed thing for the yes to be about.
"""

from __future__ import annotations

from typing import Protocol

from dispute_agent.kernel.domain.actions import ActionIntent
from dispute_agent.kernel.domain.enums import IntentStatus
from dispute_agent.kernel.domain.ids import IntentId


class IntentStore(Protocol):
    def add(self, intent: ActionIntent) -> None: ...

    def get(self, intent_id: IntentId) -> ActionIntent:
        """Raises NotFound."""
        ...

    def set_status(self, intent_id: IntentId, status: IntentStatus) -> None: ...
