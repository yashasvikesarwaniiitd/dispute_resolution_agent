"""`make agents-sync`: make Foundry's agent match the prompt in the repo (foundry.md section 4).

Run by the owner, never by the runtime - the runtime only reads agents (I1's spirit applied
to infrastructure: the thing answering questions cannot rewrite its own instructions).

It is idempotent. If the lock already describes an agent built from exactly this prompt
and deployment, *and* Foundry still holds that version unchanged, nothing is created.
Otherwise a new version is created, read back, and only then written to the lock: a lock
entry that described a version Foundry had not actually stored would make every later
drift check pass against the wrong thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dispute_agent.kernel.domain.errors import AgentDrift, ConfigError
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import (
    AgentLock,
    AgentLockEntry,
    agent_name_for,
    definition_hash,
    read_lock,
    write_lock,
)

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
        AgentServiceClient,
    )


@dataclass(frozen=True)
class SyncResult:
    entry: AgentLockEntry
    created: bool


def sync_agent(
    client: AgentServiceClient,
    *,
    prompt_name: str,
    prompt_version: str,
    system_text: str,
    prompt_hash: str,
    deployment: str,
    lock_path: Path,
    now: datetime,
) -> SyncResult:
    if not deployment:
        raise ConfigError("FOUNDRY_MODEL_DEPLOYMENT is not set (docs/SETUP_FOUNDRY.md)")
    name = agent_name_for(prompt_name)
    wanted = definition_hash(model=deployment, instructions=system_text)
    lock = read_lock(lock_path)
    existing = lock.agents.get(prompt_name)

    if existing is not None and (existing.definition_hash, existing.prompt_hash) == (
        wanted,
        prompt_hash,
    ):
        try:
            deployed = client.get_version(existing.agent_name, existing.version)
        except AgentDrift:
            deployed = None
        if deployed is not None and (
            definition_hash(model=deployed.model, instructions=deployed.instructions) == wanted
        ):
            return SyncResult(existing, created=False)

    version = client.create_version(name, deployment, system_text)
    stored = client.get_version(name, version)
    if definition_hash(model=stored.model, instructions=stored.instructions) != wanted:
        raise AgentDrift(
            f"Foundry stored {name} v{version} differently from what was sent; the lock was "
            "not updated"
        )

    entry = AgentLockEntry(
        agent_name=name,
        version=version,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        prompt_hash=prompt_hash,
        definition_hash=wanted,
        deployment=deployment,
        structured_output=False,
        synced_at=now,
    )
    write_lock(lock_path, AgentLock(agents={**lock.agents, prompt_name: entry}))
    return SyncResult(entry, created=True)


__all__ = ["SyncResult", "sync_agent"]
