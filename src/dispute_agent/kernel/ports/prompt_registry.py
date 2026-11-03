"""The PromptRegistry port (kernel-interfaces.md section 6.4).

Runtime prompts live in `prompts/`, never inline in `src/` — an architecture test enforces
it. Untrusted values reach a prompt only through this port, inside delimited blocks (I5).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from dispute_agent.kernel.domain.json_values import JsonValue
from dispute_agent.kernel.domain.reasoning import RenderedPrompt
from dispute_agent.kernel.domain.values import UntrustedText


class PromptRegistry(Protocol):
    def load(
        self,
        name: str,
        version: str,
        variables: Mapping[str, JsonValue],
        untrusted: Mapping[str, UntrustedText],
    ) -> RenderedPrompt:
        """Renders `prompts/<name>/<version>/`.

        `variables` and `untrusted` are separate arguments deliberately: a value's trust label
        is decided by which argument it was passed in, not by a guess at render time.

        Raises NotFound, DomainValidationError.
        """
        ...
