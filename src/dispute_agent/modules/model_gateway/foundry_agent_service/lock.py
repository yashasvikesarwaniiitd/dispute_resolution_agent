"""The agent lock file: what we expect Foundry to be running (foundry.md section 4).

`make agents-sync` writes one entry per synced prompt; the runtime reads it and refuses to
call an agent that does not match. The file is committed and holds no secrets - it is a
record of *which* agent version the code was built against, like a dependency lock.

`definition_hash` is the drift check's fingerprint. It covers everything that changes what
the agent does - the deployment it runs on, its instructions, its output format and its
tools - so an edit to any of them in the Azure portal shows up as a mismatch.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from dispute_agent.kernel.domain.errors import ConfigError
from dispute_agent.kernel.domain.hashing import payload_hash

if TYPE_CHECKING:
    from pathlib import Path

_FROZEN = ConfigDict(frozen=True, extra="forbid")

#: Foundry: alphanumeric at both ends, hyphens inside, at most 63 characters (SDK docstring
#: for `create_version`). foundry.md's `wd-<prompt_name>` would contain an underscore,
#: which Foundry refuses, so underscores become hyphens (ADR 0029 addendum).
AGENT_NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


def agent_name_for(prompt_name: str) -> str:
    name = "wd-" + prompt_name.replace("_", "-")
    if not AGENT_NAME.match(name):
        raise ConfigError(f"{name!r} is not a valid Foundry agent name")
    return name


def definition_hash(*, model: str, instructions: str) -> str:
    """The fingerprint the drift check compares. No text format and no tools, by design
    (ADR 0029: structured output is not supported on Agent Service; D12: no tools)."""
    return payload_hash(
        {"model": model, "instructions": instructions, "text_format": None, "tools": []}
    )


class AgentLockEntry(BaseModel):
    model_config = _FROZEN

    agent_name: str = Field(pattern=AGENT_NAME.pattern)
    version: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str
    definition_hash: str
    deployment: str
    structured_output: bool = False
    synced_at: datetime


class AgentLock(BaseModel):
    model_config = _FROZEN

    agents: dict[str, AgentLockEntry] = Field(default_factory=dict)

    def entry(self, prompt_name: str) -> AgentLockEntry:
        found = self.agents.get(prompt_name)
        if found is None:
            raise ConfigError(
                f"no agent is synced for prompt {prompt_name!r}; run `make agents-sync` "
                "(docs/SETUP_FOUNDRY.md)"
            )
        return found


def read_lock(path: Path) -> AgentLock:
    if not path.is_file():
        return AgentLock()
    return AgentLock.model_validate_json(path.read_text(encoding="utf-8"))


def write_lock(path: Path, lock: AgentLock) -> None:
    body = json.dumps(lock.model_dump(mode="json"), indent=2, sort_keys=True)
    path.write_text(body + "\n", encoding="utf-8")


__all__ = [
    "AgentLock",
    "AgentLockEntry",
    "agent_name_for",
    "definition_hash",
    "read_lock",
    "write_lock",
]
