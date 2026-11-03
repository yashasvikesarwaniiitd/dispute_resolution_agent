"""The ModuleStatus port (kernel-interfaces.md section 6.1, I6).

Answers "is this module switched on?" from the registry built at startup, so a disabled module
sends the case to a human with a named reason rather than being skipped quietly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ModuleStatus(Protocol):
    def is_enabled(self, module: str) -> bool:
        """Pre: module is a registered name.

        Raises ConfigError for an unknown name, which is a programming error rather than a
        runtime condition — asking about a module that does not exist means the caller is
        wrong, and failing loudly is kinder than returning False.
        """
        ...

    def adapter_name(self, module: str) -> str:
        """Which adapter is wired in, for logs and the health endpoint."""
        ...
