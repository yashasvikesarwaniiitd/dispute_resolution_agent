"""`make foundry-check`: is everything in place to call the agent? Costs nothing.

Checks each piece in the order the setup guide builds them, says which one is missing, and
stops there - a list of ten failures is harder to act on than the first one. It makes no
model call; `make try-agent` does that.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

from dispute_agent.app import (
    DEFAULT_CONFIG_DIR,
    FOUNDRY_AUTH_RECORD,
    FOUNDRY_LOCK,
    MODEL_CALL_LOG,
    PRICING,
    PROMPTS_DIR,
    build_model_policy,
)
from dispute_agent.infra.settings import Settings
from dispute_agent.kernel.domain.errors import KernelError
from dispute_agent.kernel.domain.hashing import payload_hash
from dispute_agent.modules.model_gateway.foundry_agent_service.gateway import ROUTE
from dispute_agent.modules.model_gateway.foundry_agent_service.lock import (
    definition_hash,
    read_lock,
)
from dispute_agent.modules.model_gateway.foundry_agent_service.pricing import load_price
from dispute_agent.modules.reasoning.prompt_registry import FilePromptRegistry

PROMPT, VERSION = "case_investigator", "v1"


def ok(message: str) -> None:
    print(f"  OK       {message}")


def stop(message: str, fix: str) -> int:
    print(f"  MISSING  {message}\n           Fix: {fix}")
    return 1


def main() -> int:
    # Windows consoles default to cp1252, which has no rupee sign.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    print("Foundry check (no model call, no cost)\n")
    settings = Settings()

    if not settings.foundry_project_endpoint:
        return stop(
            "FOUNDRY_PROJECT_ENDPOINT", "copy the project endpoint into .env (setup step 5)"
        )
    if not settings.foundry_project_endpoint.startswith("https://"):
        return stop(
            "FOUNDRY_PROJECT_ENDPOINT looks wrong",
            "it starts with https:// and ends /api/projects/<name>",
        )
    ok("project endpoint is set")
    if not settings.foundry_model_deployment:
        return stop("FOUNDRY_MODEL_DEPLOYMENT", "put your deployment's name in .env (setup step 4)")
    ok(f"deployment name is {settings.foundry_model_deployment}")

    try:
        policy = build_model_policy(DEFAULT_CONFIG_DIR, settings)
    except KernelError as bad:
        return stop(f"policy settings: {bad}", "check the LLM_* and AZURE_* lines in .env")
    expires = policy.llm_primary.credit_expires_on
    if expires is None and not policy.llm_primary.allow_paid_usage:
        return stop(
            "AZURE_CREDIT_EXPIRES_ON", "write your credit's end date in .env (setup step 7)"
        )
    if expires is not None:
        days = (expires - datetime.now(UTC).date()).days
        ok(
            f"credit window ends {expires} ({days} days left); paid usage "
            f"{'ALLOWED' if policy.llm_primary.allow_paid_usage else 'off'}"
        )

    try:
        price = load_price(PRICING, ROUTE, settings.foundry_model_deployment)
    except KernelError as missing:
        return stop(str(missing), "fill in config/pricing.yaml (setup step 'Prices')")
    ok(f"price row found (source: {price.source})")

    registry = FilePromptRegistry(PROMPTS_DIR, allow_inactive=settings.prompts_allow_inactive)
    status = str(registry.meta(PROMPT, VERSION).get("status", "inactive")).split()[0]
    if status != "active" and not settings.prompts_allow_inactive:
        return stop(
            f"{PROMPT}/{VERSION} is '{status}'",
            "set PROMPTS_ALLOW_INACTIVE=true to test it knowingly",
        )
    ok(f"prompt {PROMPT}/{VERSION} ({status}{', opt-in on' if status != 'active' else ''})")

    from dispute_agent.modules.model_gateway.foundry_agent_service.signin import (
        SCOPE,
        check_tenant,
        runtime_credential,
    )

    try:
        check_tenant(settings.azure_tenant_id)
    except KernelError:
        return stop("AZURE_TENANT_ID", "Entra ID > Overview > Tenant ID, into .env (setup step 9)")
    ok("tenant id is set")
    try:
        credential = runtime_credential(settings.azure_tenant_id, FOUNDRY_AUTH_RECORD)
        credential.get_token(SCOPE)
    except Exception as failed:
        return stop(f"Foundry sign-in ({type(failed).__name__})", "run `make foundry-login`")
    ok("signed in to Azure (Entra ID, from the encrypted cache)")

    lock = read_lock(FOUNDRY_LOCK)
    entry = lock.agents.get(PROMPT)
    if entry is None:
        return stop("no synced agent", "run `make agents-sync`")
    if entry.prompt_hash != payload_hash(registry.system_text(PROMPT, VERSION)):
        return stop("the prompt changed since the last sync", "run `make agents-sync`")
    ok(f"lock names {entry.agent_name} version {entry.version}")

    from dispute_agent.modules.model_gateway.foundry_agent_service.client import (
        AzureAgentServiceClient,
    )

    try:
        deployed = AzureAgentServiceClient(
            settings.foundry_project_endpoint, timeout_s=30, credential=credential
        ).get_version(entry.agent_name, entry.version)
    except KernelError as failed:
        return stop(
            f"reading the agent from Foundry: {failed}",
            "check your role (Foundry User) and the endpoint",
        )
    if (
        definition_hash(model=deployed.model, instructions=deployed.instructions)
        != entry.definition_hash
    ):
        return stop(
            "the agent in Foundry differs from the lock (edited in the portal?)",
            "run `make agents-sync`",
        )
    ok(f"agent in Foundry matches the lock exactly (model {deployed.model})")

    if MODEL_CALL_LOG.is_file():
        from dispute_agent.infra.sqlite.model_call_log import open_model_call_log

        spent = open_model_call_log(MODEL_CALL_LOG).spend_usd(ROUTE)
        ok(f"spent so far ${spent:.4f} of the ${policy.llm_primary.spend_cap_usd} cap")

    print("\nReady. Try it with: make try-agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
