"""`make agents-sync`: create or update the Foundry agent from the prompt in the repo.

Run by the owner, after `az login` (foundry.md section 4, docs/SETUP_FOUNDRY.md). It changes
Foundry; nothing else in the project does.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from dispute_agent.app import DEFAULT_CONFIG_DIR, FOUNDRY_LOCK, PROMPTS_DIR, build_model_policy
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
    AzureAgentServiceClient,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.sync import sync_agent
from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

PROMPT, VERSION = "case_investigator", "v1"


def main() -> int:
    # Windows consoles default to cp1252, which has no rupee sign.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    settings = Settings()
    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=settings.prompts_allow_inactive)
    status = str(registry.meta(PROMPT, VERSION).get("status", "inactive")).split()[0]
    if status != "active" and not settings.prompts_allow_inactive:
        print(
            f"{PROMPT}/{VERSION} is '{status}': it has not passed its eval suite yet.\n"
            "To sync it anyway for prototype testing, set PROMPTS_ALLOW_INACTIVE=true in .env."
        )
        return 2
    if not settings.foundry_project_endpoint or not settings.foundry_model_deployment:
        print("Set FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_MODEL_DEPLOYMENT in .env first.")
        return 2

    policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    system = registry.system_text(PROMPT, VERSION)
    try:
        result = sync_agent(
            AzureAgentServiceClient(
                settings.foundry_project_endpoint, timeout_s=policy.llm.call_timeout_s
            ),
            prompt_name=PROMPT,
            prompt_version=VERSION,
            system_text=system,
            prompt_hash=payload_hash(system),
            deployment=settings.foundry_model_deployment,
            lock_path=FOUNDRY_LOCK,
            now=datetime.now(UTC),
        )
    except KernelError as failed:
        print(f"agents-sync stopped: {failed}", file=sys.stderr)
        return 1

    entry = result.entry
    if result.created:
        print(f"Created {entry.agent_name} version {entry.version} on {entry.deployment}.")
        print(f"Recorded it in {FOUNDRY_LOCK}. Commit that file.")
    else:
        print(
            f"{entry.agent_name} version {entry.version} already matches the prompt. Nothing to do."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
