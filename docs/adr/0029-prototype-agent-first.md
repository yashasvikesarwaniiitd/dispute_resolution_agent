# 0029 — A prototype Foundry agent first, then improve it

Date: 2026-11-04
Status: accepted (owner decision, 2026-11-04: "Start building, don't ask any question just build this")
Context: the owner needs to start working with the agent in Microsoft Foundry. The frozen plan
(`arch-v1`) reached Foundry only after Phase 4 was complete.

## Decision

The remaining phases are re-ordered. Nothing is removed.

| Order | Phase | Content |
|---|---|---|
| 1 | **4P — Prototype agent** | The thinnest safe slice of Phases 4 and 4b: the Foundry agent proposes the next step for a case at the investigate station, behind every existing safety rail. |
| 2 | 4+ — Make the agent trustworthy | The rest of Phases 4 and 4b: Haiku failover, guardrail screen, tracing, live evals and model choice, credit-window reporting, the model reply interpreter and drafter, two-pass anchoring. |
| 3 | 5 — Credits and sending | Phase 5, plus the Phase 3 carry-over: apply_approval, execute and verify steps, the SQLite ActionStore, declared timer effects, investigate audit events. |
| 4 | 6 — Full testing | Unchanged, including Foundry evals and red team. |
| 5 | 7 — Cross-case patterns | Unchanged. |

Phase 4P is built **offline first**: everything is developed and tested against a fake model
gateway, and only the connection to Foundry needs the owner's Azure account. That keeps the
30-day credit window (D18) for the work that actually needs a live model.

### Phase 4P cards (pulled forward, scope narrowed where marked)

- P4-01 PromptRegistry and the `neutralise` filter
- P4-02 CaseBriefing builder (already built inside the investigate step; kept there)
- P4-03 ModelGateway request and response shapes (already defined; used as they are)
- P4-04 ModelGateway fake (contract suite filled in; record-replay deferred)
- P4-05 LlmReasoner and proposal validation, with the rules-only baseline as fallback
- P4-09 Injection defences — core only: delimited untrusted blocks, the neutralise filter, the
  corpus items already in the repo. The full corpus and screen are in 4+.
- P4b-01 Foundry scope and documentation check — this ADR
- P4b-02 Model-call recording
- P4b-03 FoundryAgentServiceGateway
- P4b-04 Agent sync, lock file and drift check
- P4b-07 Budgets — core only: a per-case call cap and a total spend cap, from the first call
- P4b-12 Secret-scanning test
- P4b-13 `make foundry-check` and the owner setup guide

### Non-negotiable in the prototype

The AI proposes; code decides (I1), and the proposal must be on the case's menu. Seller text
reaches the model only inside a delimited, neutralised block (I5). Malformed output escalates.
A spending cap applies from the first call. Foundry is reached with Entra ID only (I14). The
prototype cannot send anything: the sending stations are Phase 5.

## Documentation check (P4b-01), read 2026-11-04

Confirmed against Microsoft's live documentation:

- `azure-ai-projects` 2.10.0 (2026-10-30) is current; 2.x remains the major version.
  [PyPI](https://pypi.org/project/azure-ai-projects/)
- Agents are created with `project.agents.create_version(agent_name=..., definition=PromptAgentDefinition(model=..., instructions=...))`.
  [Prompt agent quickstart](https://learn.microsoft.com/en-us/azure/foundry/agents/quickstarts/prompt-agent)
- Entra ID is the only authentication the projects client supports; `DefaultAzureCredential`.
  [azure-ai-projects readme](https://learn.microsoft.com/en-us/python/api/overview/azure/ai-projects-readme?view=azure-python)
- `gpt-5-mini` (2025-08-07) is available as Global Standard in South India, and Agent Service
  runs there. [Region availability](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability),
  [Agent limits and regions](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/limits-quotas-regions)
- `gpt-5-mini` retires on **2027-07-13**, so it is not a soon-retired model.
  [Retirement schedule](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/model-retirement-schedule)
- Roles: **Foundry User** to create agents; **Foundry Agent Consumer** to call them only.
  [Foundry RBAC](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
- `store=False` is supported on the agent call: "the service doesn't persist the response".
  [Runtime components](https://learn.microsoft.com/en-us/azure/foundry/agents/concepts/runtime-components)

## Where the documentation differs from `foundry.md`, and what we do

1. **Invocation.** `foundry.md` section 3 calls the agent with `extra_body={"agent_reference": {...}}`.
   The current Python documentation binds the agent with `project.get_openai_client(agent_name=...)`
   and calls `responses.create(input=..., store=False)`. We follow the documentation.
2. **Version pinning is not documented.** `foundry.md` pins the agent version on every call.
   No current example shows a version on the agent reference, so we cannot rely on it.
   Instead, **the drift check runs before every call**: the agent's latest version in Foundry
   must equal the version in our lock file, and its instructions must hash to our prompt's
   hash. Anything else raises `AgentDrift` and the case goes to a person. This is stricter
   than pinning, not looser: an agent edited in the portal is refused rather than quietly
   bypassed.
3. **Structured output is not supported with Agent Service** ([structured outputs](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/structured-outputs)).
   `foundry.md` already anticipated this: output is validated in our code against the
   prompt's schema, always, and the lock file records `structured_output: false`.
4. **Token usage fields** on the response are not shown in the agent documentation. The
   gateway reads them when present and otherwise charges the call at its worst case
   (`max_output_tokens`) against the spending cap, so missing usage can only make the cap
   stricter.

The `docs/spec/` files are not edited; this ADR records the deviations, as `CLAUDE.md` section
18 requires.

## Consequences

- The owner can work with a real agent after one phase instead of two.
- The credit window starts later, when there is something ready to connect.
- The Phase 6 Foundry evals may fall after the credit window; at that point the owner decides
  whether to allow paid usage (`LLM_ALLOW_PAID_USAGE`), which is off by default.

## Addendum, 2026-11-04: what the installed SDK showed

Reading `azure-ai-projects` 2.10.0's own code, after installing it, corrected deviations 1 and 2
above. The original `foundry.md` design stands:

1. **Invocation.** `get_openai_client(agent_name=...)` requires `allow_preview=True` in 2.10.0
   (`_patch.py`, `get_openai_client` docstring), so it is a preview path and is not used. The
   agent is called through the GA project client, `project.get_openai_client()`, with
   `extra_body={"agent_reference": {"name", "version", "type": "agent_reference"}}`. The SDK's
   own `AgentReference` model carries `version`, and its telemetry reads the same `extra_body` key.
2. **Version pinning is available, and used.** Every call names the version from the lock file.
   The drift check reads that exact version with `agents.get_version(name, version)` and compares
   its fingerprint, cached for 60 seconds (`foundry.md` section 4).
3. **Agent name.** Foundry agent names allow letters, digits and inner hyphens only
   (`create_version` docstring), so `foundry.md`'s `wd-case_investigator` would be refused. The
   agent is `wd-case-investigator`.

Deviations 3 (no structured output, so output is always validated in code) and 4 (missing usage
is charged at the worst case) are unchanged.

## Addendum: the prototype's front door

The long-running runtime gains the investigate station with the sending stations in Phase 5.
For the prototype, `make try-agent` runs one synthetic case through the full line with the agent
deciding, in a throwaway database, with no channel wired, so nothing can be sent
(`src/dispute_agent/api/agent_trial.py`). `make try-agent ARGS="--offline"` runs the same path
with the scripted fake.
