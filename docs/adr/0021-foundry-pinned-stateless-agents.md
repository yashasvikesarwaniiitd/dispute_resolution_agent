# ADR 0021: Foundry agents are called by pinned version, statelessly, and validated in our code

Status: accepted (owner, 2026-11-02, D37)
Date: 2026-11-02

## Decision
One tool-less prompt agent per runtime prompt, created only by `make agents-sync` from `prompts/`, recorded in `config/foundry_agents.lock.json`. Every call uses the Responses API with an `agent_reference` whose version is pinned from the lock, `store=false`, no conversation, and no sampling parameters. Before calls the adapter checks the pinned definition's hash against the lock (`AgentDrift` otherwise). Output is always validated against our full schema; a strict-mode-compatible provider schema is derived for the provider. If agent structured outputs are rejected, the lock records it and `foundry_openai` (same deployment, Responses API without an agent) can be selected as the primary by configuration.

## Why
Pinning and the drift check make the hosted agent provably the same as the reviewed prompt (D12). Statelessness keeps case state in our database only. Microsoft's docs conflict on structured outputs for agents, so correctness must not depend on the provider enforcing the schema.

## Options considered
- Unpinned agent references: behaviour undocumented.
- Conversations or stored responses: would put case context in Foundry, against D12.
- Only `foundry_openai`: simpler, but loses the agent registry and versioning the owner chose (D12).

## Consequences
Easier: replayable, auditable calls. Harder: the owner runs a sync step after prompt changes. Watch: Phase 4b measures the invalid-output rate with and without provider enforcement.

## Sources
https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent · https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/aiproject · https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components · https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/responses · https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs · https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions
